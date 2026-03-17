from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings
from django.utils import timezone
from .models import (
    StudentProfile, Document, ApplicationStep,
    UpcomingDate, Reminder, Announcement, NewApplication, RenewalApplication, Office,
    ActiveStudentAssistant, AttendanceRecord, PerformanceEvaluation,
    ApplicationNote, NoDutyDay, DutyReminder,
    calculate_end_date, recalculate_end_dates_for_office, auto_expire_student_assistants,
    generate_absent_records_for_yesterday,
)
from .forms import (
    ReminderForm, UpcomingDateForm, AnnouncementForm, NewApplicationForm,
    RenewalApplicationForm, OfficeForm, AttendanceForm, PerformanceEvaluationForm,
    ActiveSAStatusForm, ScheduleResubmitForm, DocumentResubmitForm,
    StudentLoginForm, NoDutyDayForm,
    DAY_CHOICES, TIME_SLOT_CHOICES,
)
from .email_utils import (
    send_application_confirmation, send_status_update_email,
    send_schedule_mismatch_email, send_document_request_email,
    send_verification_email,
)
from datetime import date as _date, datetime as _datetime, timedelta
import json
import csv
import calendar
from collections import defaultdict
from decimal import Decimal
import base64
import os
import uuid
import cv2
import numpy as np
from django.core.files.base import ContentFile


def _inject_camera_photos(request, doc_fields):
    """Move camera-captured photos from hidden POST fields into request.FILES.

    For each document field, check if ``<field>_photo`` exists in POST data
    (a filename saved by process_camera_photo). If the corresponding file
    input is empty (no regular upload), open the camera photo from disk and
    inject it as an InMemoryUploadedFile so the form sees a normal file.
    """
    # request.FILES may be immutable; ensure we can write to it
    mutable_before = getattr(request.FILES, '_mutable', None)
    if mutable_before is not None:
        request.FILES._mutable = True

    for field in doc_fields:
        photo_key = f"{field}_photo"
        filename = request.POST.get(photo_key, '').strip()
        if not filename or field in request.FILES:
            continue  # no camera photo, or user already uploaded a file
        photo_path = os.path.join(settings.MEDIA_ROOT, 'camera_photos', filename)
        if not os.path.isfile(photo_path):
            continue
        # Security: ensure filename doesn't escape the camera_photos dir
        real_dir = os.path.realpath(os.path.join(settings.MEDIA_ROOT, 'camera_photos'))
        real_path = os.path.realpath(photo_path)
        if not real_path.startswith(real_dir):
            continue
        with open(photo_path, 'rb') as fh:
            request.FILES[field] = ContentFile(fh.read(), name=filename)

    if mutable_before is not None:
        request.FILES._mutable = mutable_before


def _urgency_for_days(days_left):
    """Return urgency level string based on days remaining."""
    if days_left < 0:
        return 'passed'
    elif days_left <= 3:
        return 'critical'
    elif days_left <= 7:
        return 'urgent'
    elif days_left <= 14:
        return 'soon'
    return 'normal'


def _validate_uploaded_file(file_field, field_name):
    """Run OpenCV quality checks on stored uploaded file. Returns dict with results."""
    result = {'warnings': [], 'checks': {}}
    try:
        if not file_field or not file_field.name:
            return result
        ext = ''
        if '.' in file_field.name:
            ext = ('.' + file_field.name.rsplit('.', 1)[-1]).lower()
        is_image = ext in ('.jpg', '.jpeg', '.png')
        if not is_image:
            return result

        file_field.open('rb')
        file_bytes = file_field.read()
        file_field.close()

        np_arr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is None:
            result['warnings'].append('Could not decode image — file may be corrupted.')
            result['checks']['decodable'] = False
            return result

        result['checks']['decodable'] = True
        h, w = img.shape[:2]
        result['checks']['resolution'] = f'{w}x{h}'

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Blur detection
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        result['checks']['blur_score'] = round(lap_var, 2)
        if lap_var < 50.0:
            result['warnings'].append(f'Blurry (sharpness: {lap_var:.0f})')
            result['checks']['blur_ok'] = False
        else:
            result['checks']['blur_ok'] = True

        # Blank detection
        std_dev = gray.std()
        result['checks']['contrast_score'] = round(std_dev, 2)
        if std_dev < 15.0:
            result['warnings'].append('Appears blank or nearly blank')
            result['checks']['blank_ok'] = False
        else:
            result['checks']['blank_ok'] = True

        # Face detection for ID pictures
        if field_name == 'id_picture':
            try:
                cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                face_cascade = cv2.CascadeClassifier(cascade_path)
                faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30))
                num = len(faces) if faces is not None else 0
                result['checks']['faces_detected'] = num
                if num == 0:
                    result['warnings'].append('No face detected')
                    result['checks']['face_ok'] = False
                elif num > 1:
                    result['warnings'].append(f'{num} faces detected (expected 1)')
                    result['checks']['face_ok'] = False
                else:
                    result['checks']['face_ok'] = True
            except Exception:
                pass
    except Exception:
        pass
    return result


def _create_active_sa_from_application(app):
    """
    Create an ActiveStudentAssistant record from an approved application.
    Handles both NewApplication and RenewalApplication.
    Idempotent — skips if a record already exists.
    """
    is_renewal = isinstance(app, RenewalApplication)

    # Check if record already exists
    if is_renewal:
        if ActiveStudentAssistant.objects.filter(renewal_application=app).exists():
            return
    else:
        if ActiveStudentAssistant.objects.filter(new_application=app).exists():
            return

    # Resolve the office FK from the CharField assigned_office
    office_fk = None
    if app.assigned_office:
        office_fk = Office.objects.filter(name=app.assigned_office, is_active=True).first()
    if not office_fk and app.preferred_office:
        office_fk = app.preferred_office

    # Build the full_name
    if is_renewal:
        full_name = app.full_name
    else:
        full_name = f"{app.first_name} {app.middle_initial}. {app.last_name}"
        if app.extension_name:
            full_name += f" {app.extension_name}"

    sa = ActiveStudentAssistant(
        student_id=app.student_id,
        full_name=full_name,
        email=app.email,
        course=app.course,
        assigned_office=office_fk,
        semester=app.semester,
        start_date=app.start_date,
        status='active',
        duty_schedule=app.availability_schedule,
    )

    # Auto-calculate end_date (80 weekdays, skipping no-duty days)
    if app.start_date:
        from django.db.models import Q as _Q
        ndd_qs = NoDutyDay.objects.filter(
            _Q(office=office_fk) | _Q(office__isnull=True)
        )
        no_duty_dates = list(ndd_qs.values_list('date', flat=True))
        sa.end_date = calculate_end_date(app.start_date, duty_days=80, no_duty_dates=no_duty_dates)

    if is_renewal:
        sa.renewal_application = app
    else:
        sa.new_application = app

    sa.save()


def _build_documents_from_app(app):
    """Build document status list from a NewApplication's file fields."""
    doc_fields = [
        ('application_form', 'Application Form'),
        ('id_picture', '2x2 ID Picture'),
        ('barangay_clearance', 'Barangay Clearance'),
        ('parents_itr', "Parent's ITR / Certificate of Indigency"),
        ('enrolment_form', 'Certificate of Enrolment'),
        ('schedule_classes', 'Schedule of Classes'),
        ('proof_insurance', 'Proof of Insurance'),
        ('grades_last_sem', 'Grades Last Semester'),
        ('official_time', 'Official Time'),
    ]
    documents = []
    for field_name, label in doc_fields:
        # Official Time is the availability schedule, not a file upload
        if field_name == 'official_time':
            sched = app.availability_schedule
            has_schedule = bool(sched and isinstance(sched, dict) and len(sched) > 0)
            if has_schedule:
                status = 'done' if app.status in ('approved', 'office_assigned') else 'uploaded'
                lbl = 'Done' if status == 'done' else 'Uploaded'
                documents.append({'name': label, 'field': field_name, 'uploaded': True, 'status': status, 'label': lbl, 'url': '', 'is_schedule': True})
            else:
                documents.append({'name': label, 'field': field_name, 'uploaded': False, 'status': 'missing', 'label': 'Missing', 'url': ''})
            continue
        file_field = getattr(app, field_name)
        if file_field:
            url = file_field.url
            if app.status in ('approved', 'office_assigned'):
                documents.append({'name': label, 'field': field_name, 'uploaded': True, 'status': 'done', 'label': 'Done', 'url': url})
            else:
                documents.append({'name': label, 'field': field_name, 'uploaded': True, 'status': 'uploaded', 'label': 'Uploaded', 'url': url})
        else:
            documents.append({'name': label, 'field': field_name, 'uploaded': False, 'status': 'missing', 'label': 'Missing', 'url': ''})
    return documents


def _build_documents_from_renewal(app):
    """Build document status list from a RenewalApplication's file fields."""
    doc_fields = [
        ('id_picture', '2x2 ID Picture'),
        ('enrolment_form', 'Photocopy of Enrolment Form'),
        ('schedule_classes', 'Schedule of Classes'),
        ('grades_last_sem', 'Grades Last Semester'),
        ('official_time', 'Filled Out Official Time'),
        ('recommendation_letter', 'Recommendation Letter & Budget Allocation'),
        ('evaluation_form', 'Evaluation Form'),
    ]
    documents = []
    for field_name, label in doc_fields:
        # Official Time is the availability schedule, not a file upload
        if field_name == 'official_time':
            sched = app.availability_schedule
            has_schedule = bool(sched and isinstance(sched, dict) and len(sched) > 0)
            if has_schedule:
                status = 'done' if app.status in ('approved', 'office_assigned') else 'uploaded'
                lbl = 'Done' if status == 'done' else 'Uploaded'
                documents.append({'name': label, 'field': field_name, 'uploaded': True, 'status': status, 'label': lbl, 'url': '', 'is_schedule': True})
            else:
                documents.append({'name': label, 'field': field_name, 'uploaded': False, 'status': 'missing', 'label': 'Missing', 'url': ''})
            continue
        file_field = getattr(app, field_name)
        if file_field:
            url = file_field.url
            if app.status in ('approved', 'office_assigned'):
                documents.append({'name': label, 'field': field_name, 'uploaded': True, 'status': 'done', 'label': 'Done', 'url': url})
            else:
                documents.append({'name': label, 'field': field_name, 'uploaded': True, 'status': 'uploaded', 'label': 'Uploaded', 'url': url})
        else:
            documents.append({'name': label, 'field': field_name, 'uploaded': False, 'status': 'missing', 'label': 'Missing', 'url': ''})
    return documents


def _build_steps_from_status(status):
    """Build workflow steps based on application status."""
    step_defs = [
        (1, 'Application Submitted'),
        (2, 'Document Verification'),
        (3, 'Interview & Assessment'),
        (4, 'Final Approval'),
    ]
    # Map status to the step that is currently active (1-indexed)
    status_to_current = {
        'pending': 2,                # submitted, now waiting for doc verification
        'under_review': 3,           # docs verified, now interview/assessment
        'schedule_mismatch': 2,      # schedule needs correction, back to doc phase
        'documents_requested': 2,    # additional docs needed, back to doc phase
        'interview_scheduled': 3,    # interview date set, awaiting interview
        'interview_done': 4,         # interview completed, awaiting approval
        'office_assigned': 4,        # legacy — treat same as interview_done
        'approved': 5,               # all steps done (past the last step)
        'rejected': 0,               # none active
    }
    current_step = status_to_current.get(status, 2)

    steps = []
    for num, title in step_defs:
        if num < current_step:
            steps.append({'step_number': num, 'title': title, 'status': 'done'})
        elif num == current_step:
            steps.append({'step_number': num, 'title': title, 'status': 'current'})
        else:
            steps.append({'step_number': num, 'title': title, 'status': 'locked'})
    return steps


STATUS_DISPLAY_MAP = {
    'pending': ('Waiting for Document Check', 'Your application has been submitted. Please wait while your documents are being checked and verified to determine your eligibility as a Student Assistant.'),
    'under_review': ('Under Review', "Your documents are currently being verified by the Registrar's Office."),
    'schedule_mismatch': ('Schedule Mismatch', 'Your availability schedule does not match your uploaded Schedule of Classes. Please re-submit your availability below.'),
    'documents_requested': ('Additional Documents Requested', 'Staff has requested additional documents. Please upload the required documents below.'),
    'interview_scheduled': ('Interview Scheduled', 'Your documents have been verified. Please check your scheduled interview date below.'),
    'interview_done': ('Interview Completed', 'Your interview has been completed. Please wait for the final approval and start date.'),
    'office_assigned': ('Office Assigned', 'You have been assigned to an office. Awaiting final approval with your start date.'),
    'approved': ('Approved', 'Congratulations! Your application has been approved. Check your start date below.'),
    'rejected': ('Rejected', 'Your application was not approved. Please contact the office for details.'),
}


def home(request):
    """Home/dashboard view for student applicants."""
    today = _date.today()

    # ── Handle "Track Application" form submission ──
    track_error = ''
    track_success = ''
    submission_success = request.session.pop('submission_success', None)
    if request.method == 'POST' and 'track_student_id' in request.POST:
        track_sid = request.POST.get('track_student_id', '').strip()
        if track_sid:
            # Verify the student ID actually exists before storing
            exists_new = NewApplication.objects.filter(student_id=track_sid).exists()
            exists_renew = RenewalApplication.objects.filter(student_id=track_sid).exists()
            if exists_new or exists_renew:
                tracked = request.session.get('tracked_student_ids', [])
                if track_sid not in tracked:
                    tracked.append(track_sid)
                request.session['tracked_student_ids'] = tracked
                track_success = f'Application found for Student ID {track_sid}!'
            else:
                track_error = f'No application found for Student ID "{track_sid}". Please check your ID and try again.'

    # ── Collect ALL applications for this visitor ──
    new_apps = []
    renewal_apps = []

    # 1. Check session PKs
    app_pk = request.session.get('application_pk')
    if app_pk:
        obj = NewApplication.objects.filter(pk=app_pk).first()
        if obj:
            new_apps.append(obj)

    renewal_pk = request.session.get('renewal_pk')
    if renewal_pk:
        obj = RenewalApplication.objects.filter(pk=renewal_pk).first()
        if obj:
            renewal_apps.append(obj)

    # 2. If authenticated, also find by email
    if request.user.is_authenticated:
        email_new = NewApplication.objects.filter(
            email=request.user.email
        ).order_by('-submitted_at')
        for a in email_new:
            if a not in new_apps:
                new_apps.append(a)

        email_renewal = RenewalApplication.objects.filter(
            email=request.user.email
        ).order_by('-submitted_at')
        for a in email_renewal:
            if a not in renewal_apps:
                renewal_apps.append(a)

    # 3. If a session-based new app exists, find matching renewals & vice-versa
    session_student_ids = set()
    session_emails = set()
    for a in new_apps:
        session_student_ids.add(a.student_id)
        session_emails.add(a.email)
    for a in renewal_apps:
        session_student_ids.add(a.student_id)
        session_emails.add(a.email)

    # 4. Also include any tracked student IDs from session
    tracked_ids = request.session.get('tracked_student_ids', [])
    for sid in tracked_ids:
        session_student_ids.add(sid)

    if session_student_ids:
        for a in NewApplication.objects.filter(student_id__in=session_student_ids).order_by('-submitted_at'):
            if a not in new_apps:
                new_apps.append(a)
        for a in RenewalApplication.objects.filter(student_id__in=session_student_ids).order_by('-submitted_at'):
            if a not in renewal_apps:
                renewal_apps.append(a)

    if session_emails:
        for a in NewApplication.objects.filter(email__in=session_emails).order_by('-submitted_at'):
            if a not in new_apps:
                new_apps.append(a)
        for a in RenewalApplication.objects.filter(email__in=session_emails).order_by('-submitted_at'):
            if a not in renewal_apps:
                renewal_apps.append(a)

    # ── Build unified application cards ──
    applications = []

    for app in new_apps:
        student_name = f"{app.first_name} {app.last_name}"
        documents = _build_documents_from_app(app)
        steps = _build_steps_from_status(app.status)
        display_status, status_message = STATUS_DISPLAY_MAP.get(
            app.status,
            ('Under Review', "Your documents are currently being verified.")
        )

        total_steps = len(steps)
        done_steps = sum(1 for s in steps if s['status'] == 'done')
        progress_pct = int((done_steps / total_steps) * 100) if total_steps else 0
        total_docs = len(documents)
        completed_docs = sum(1 for d in documents if d['status'] in ('uploaded', 'done'))
        pending_docs = sum(1 for d in documents if d['status'] in ('pending', 'missing'))

        applications.append({
            'obj': app,
            'app_type': 'New Application',
            'app_type_icon': 'fa-file-circle-plus',
            'app_type_class': 'new',
            'app_type_key': 'new',
            'student_name': student_name,
            'application_id': app.student_id,
            'documents': documents,
            'steps': steps,
            'application_status': display_status,
            'status_message': status_message,
            'raw_status': app.status,
            'progress_percent': progress_pct,
            'total_steps': total_steps,
            'completed_steps': done_steps,
            'total_docs': total_docs,
            'completed_docs': completed_docs,
            'pending_docs': pending_docs,
            'submitted_at': app.submitted_at,
            'schedule_mismatch_note': app.schedule_mismatch_note if app.status == 'schedule_mismatch' else '',
            'requested_documents_note': app.requested_documents_note if app.status == 'documents_requested' else '',
            'returned_documents': app.returned_documents if app.status == 'documents_requested' else {},
            'availability_schedule': app.availability_schedule or {},
        })

    for app in renewal_apps:
        documents = _build_documents_from_renewal(app)
        steps = _build_steps_from_status(app.status)
        display_status, status_message = STATUS_DISPLAY_MAP.get(
            app.status,
            ('Under Review', "Your documents are currently being verified.")
        )

        total_steps = len(steps)
        done_steps = sum(1 for s in steps if s['status'] == 'done')
        progress_pct = int((done_steps / total_steps) * 100) if total_steps else 0
        total_docs = len(documents)
        completed_docs = sum(1 for d in documents if d['status'] in ('uploaded', 'done'))
        pending_docs = sum(1 for d in documents if d['status'] in ('pending', 'missing'))

        applications.append({
            'obj': app,
            'app_type': 'Renewal Application',
            'app_type_icon': 'fa-arrows-rotate',
            'app_type_class': 'renewal',
            'app_type_key': 'renewal',
            'student_name': app.full_name,
            'application_id': app.student_id,
            'documents': documents,
            'steps': steps,
            'application_status': display_status,
            'status_message': status_message,
            'raw_status': app.status,
            'progress_percent': progress_pct,
            'total_steps': total_steps,
            'completed_steps': done_steps,
            'total_docs': total_docs,
            'completed_docs': completed_docs,
            'pending_docs': pending_docs,
            'submitted_at': app.submitted_at,
            'schedule_mismatch_note': app.schedule_mismatch_note if app.status == 'schedule_mismatch' else '',
            'requested_documents_note': app.requested_documents_note if app.status == 'documents_requested' else '',
            'returned_documents': app.returned_documents if app.status == 'documents_requested' else {},
            'availability_schedule': app.availability_schedule or {},
        })

    # Sort all applications by submitted date descending
    applications.sort(key=lambda x: x['submitted_at'], reverse=True)

    has_application = len(applications) > 0

    # ── Upcoming dates ──
    upcoming_dates = []
    db_dates = UpcomingDate.objects.filter(is_active=True).exclude(
        expires_at__isnull=False, expires_at__lt=today
    )
    if db_dates.exists():
        for d in db_dates:
            delta = (d.date - today).days
            upcoming_dates.append({
                'title': d.title,
                'date': d.date.strftime('%B %d, %Y'),
                'day': d.date.strftime('%d'),
                'month': d.date.strftime('%b').upper(),
                'days_left': max(delta, 0),
                'urgency': _urgency_for_days(delta),
            })

    # ── Reminders ──
    from django.db.models import Q
    reminder_filter = Q(student__isnull=True, is_active=True) & (
        Q(expires_at__isnull=True) | Q(expires_at__gte=today)
    )
    db_reminders = Reminder.objects.filter(reminder_filter).order_by('-created_at')
    reminders = [
        {
            'message': r.message,
            'priority': r.priority,
            'id': r.id,
            'created_at': r.created_at.strftime('%b %d, %Y'),
        }
        for r in db_reminders
    ]

    # ── Announcements ──
    db_announcements = Announcement.objects.filter(is_active=True).exclude(
        expires_at__isnull=False, expires_at__lt=today
    )[:6]
    seven_days_ago = timezone.now() - timedelta(days=7)
    announcements = [
        {
            'title': a.title,
            'summary': a.summary,
            'image': a.image,
            'published_at': a.published_at.strftime('%b %d, %Y'),
            'is_new': a.published_at >= seven_days_ago,
        }
        for a in db_announcements
    ]

    # ── All Applications (public list for Application History table) ──
    all_applications = []
    for app in NewApplication.objects.all().order_by('-submitted_at'):
        student_name = f"{app.first_name} {app.last_name}"
        documents = _build_documents_from_app(app)
        display_status, status_message = STATUS_DISPLAY_MAP.get(
            app.status,
            ('Under Review', "Your documents are currently being verified.")
        )
        total_docs = len(documents)
        completed_docs = sum(1 for d in documents if d['status'] in ('uploaded', 'done'))
        all_applications.append({
            'obj': app,
            'app_type': 'New Application',
            'app_type_icon': 'fa-file-circle-plus',
            'app_type_class': 'new',
            'app_type_key': 'new',
            'student_name': student_name,
            'application_id': app.student_id,
            'documents': documents,
            'application_status': display_status,
            'raw_status': app.status,
            'total_docs': total_docs,
            'completed_docs': completed_docs,
            'submitted_at': app.submitted_at,
            'schedule_mismatch_note': app.schedule_mismatch_note if app.status == 'schedule_mismatch' else '',
            'requested_documents_note': app.requested_documents_note if app.status == 'documents_requested' else '',
            'assigned_office': app.assigned_office or (app.preferred_office.name if app.preferred_office else '—'),
        })
    for app in RenewalApplication.objects.all().order_by('-submitted_at'):
        documents = _build_documents_from_renewal(app)
        display_status, status_message = STATUS_DISPLAY_MAP.get(
            app.status,
            ('Under Review', "Your documents are currently being verified.")
        )
        total_docs = len(documents)
        completed_docs = sum(1 for d in documents if d['status'] in ('uploaded', 'done'))
        all_applications.append({
            'obj': app,
            'app_type': 'Renewal Application',
            'app_type_icon': 'fa-arrows-rotate',
            'app_type_class': 'renewal',
            'app_type_key': 'renewal',
            'student_name': app.full_name,
            'application_id': app.student_id,
            'documents': documents,
            'application_status': display_status,
            'raw_status': app.status,
            'total_docs': total_docs,
            'completed_docs': completed_docs,
            'submitted_at': app.submitted_at,
            'schedule_mismatch_note': app.schedule_mismatch_note if app.status == 'schedule_mismatch' else '',
            'requested_documents_note': app.requested_documents_note if app.status == 'documents_requested' else '',
            'assigned_office': app.assigned_office or '—',
        })
    all_applications.sort(key=lambda x: x['submitted_at'], reverse=True)

    # ── Approved Student Assistants (public list) ──
    approved_new = NewApplication.objects.filter(status='approved').order_by('-submitted_at')
    approved_renewal = RenewalApplication.objects.filter(status='approved').order_by('-submitted_at')
    approved_students = []
    for app in approved_new:
        approved_students.append({
            'name': f"{app.first_name} {app.last_name}",
            'student_id': app.student_id,
            'course': app.course,
            'office': app.assigned_office or '—',
            'start_date': app.start_date,
            'submitted_at': app.submitted_at,
        })
    for app in approved_renewal:
        approved_students.append({
            'name': app.full_name,
            'student_id': app.student_id,
            'course': app.course,
            'office': app.assigned_office or '—',
            'start_date': app.start_date,
            'submitted_at': app.submitted_at,
        })
    approved_students.sort(key=lambda x: x['submitted_at'], reverse=True)

    # Get logged-in student's ID for unmasking their own data
    logged_in_student_id = ''
    if request.user.is_authenticated and hasattr(request.user, 'student_profile'):
        logged_in_student_id = request.user.student_profile.student_id

    context = {
        'applications': applications,
        'all_applications': all_applications,
        'has_application': has_application,
        'upcoming_dates': upcoming_dates,
        'reminders': reminders,
        'announcements': announcements,
        'approved_students': approved_students,
        'track_error': track_error,
        'track_success': track_success,
        'submission_success': submission_success,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
        'logged_in_student_id': logged_in_student_id,
    }
    return render(request, 'home/home.html', context)


def available_offices(request):
    """GIS campus map with available offices — real data from DB."""
    offices_qs = Office.objects.filter(is_active=True)

    # Count filled slots per office from both application types
    # (students with status office_assigned or approved)
    offices_data = []
    for office in offices_qs:
        filled_new = NewApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).count()
        filled_renewal = RenewalApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).count()
        filled = filled_new + filled_renewal
        available = max(0, office.total_slots - filled)

        if filled >= office.total_slots:
            status = 'full'
        elif available <= 1 and office.total_slots > 1:
            status = 'limited'
        else:
            status = 'open'

        # Get list of assigned students for this office
        assigned_students = []
        for app in NewApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).order_by('last_name'):
            assigned_students.append({
                'name': f"{app.first_name} {app.last_name}",
                'student_id': app.student_id,
                'status': app.get_status_display(),
                'status_key': app.status,
                'photo': app.id_picture.url if app.id_picture else '',
            })
        for app in RenewalApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).order_by('full_name'):
            assigned_students.append({
                'name': app.full_name,
                'student_id': app.student_id,
                'status': app.get_status_display(),
                'status_key': app.status,
                'photo': app.id_picture.url if app.id_picture else '',
            })

        offices_data.append({
            'id': office.pk,
            'name': office.name,
            'building': office.building,
            'room': office.room,
            'hours': office.hours,
            'head': office.head,
            'total_slots': office.total_slots,
            'filled': filled,
            'available': available,
            'status': status,
            'lat': office.latitude,
            'lng': office.longitude,
            'icon': office.icon,
            'description': office.description,
            'students': assigned_students,
        })

    # All approved / active student assistants
    all_student_assistants = ActiveStudentAssistant.objects.select_related(
        'assigned_office'
    ).filter(status='active').order_by('full_name')

    context = {
        'offices_json': json.dumps(offices_data),
        'total_offices': offices_qs.count(),
        'total_open': sum(1 for o in offices_data if o['status'] == 'open'),
        'total_limited': sum(1 for o in offices_data if o['status'] == 'limited'),
        'total_full': sum(1 for o in offices_data if o['status'] == 'full'),
        'total_approved_sa': all_student_assistants.count(),
        'all_student_assistants': all_student_assistants,
    }

    # If staff is logged in, include the office form for management
    is_staff_user = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
    if is_staff_user:
        context['is_staff_user'] = True
        context['office_form'] = OfficeForm()

    # Director flag (superuser) — enables draggable markers
    is_director = request.user.is_authenticated and request.user.is_superuser
    if is_director:
        context['is_director'] = True

    return render(request, 'home/available_offices.html', context)


def apply_new(request):
    """Application form for new student assistants."""
    if request.method == 'POST':
        # Inject camera-captured photos into request.FILES before form binding
        _inject_camera_photos(request, [
            'application_form', 'id_picture', 'barangay_clearance',
            'parents_itr', 'enrolment_form', 'schedule_classes',
            'proof_insurance', 'grades_last_sem',
        ])
        form = NewApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            application = form.save(commit=False)
            # Link authenticated student
            if request.user.is_authenticated and hasattr(request.user, 'student_profile'):
                application.user = request.user
            application.save()
            request.session['application_pk'] = application.pk
            # Persist student_id in session for reliable lookup
            tracked = request.session.get('tracked_student_ids', [])
            if application.student_id not in tracked:
                tracked.append(application.student_id)
            request.session['tracked_student_ids'] = tracked
            # Send confirmation email
            send_application_confirmation(application, app_type='new')
            request.session['submission_success'] = f'Your new application has been submitted successfully! A confirmation email has been sent to {application.email}. Please wait while your documents are being reviewed to determine your eligibility as a Student Assistant.'
            return redirect('home:home')
    else:
        form = NewApplicationForm()

    # Build available offices list with slot info for the template
    available_offices_list = []
    for office in Office.objects.filter(is_active=True).order_by('name'):
        filled_new = NewApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).count()
        filled_renewal = RenewalApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).count()
        filled = filled_new + filled_renewal
        available = max(0, office.total_slots - filled)
        available_offices_list.append({
            'id': office.pk,
            'name': office.name,
            'available': available,
        })

    return render(request, 'home/apply_new.html', {
        'form': form,
        'available_offices': available_offices_list,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
    })


def apply_renew(request):
    """Renewal form for existing student assistants."""
    if request.method == 'POST':
        # Inject camera-captured photos into request.FILES before form binding
        _inject_camera_photos(request, [
            'id_picture', 'enrolment_form', 'schedule_classes',
            'grades_last_sem', 'recommendation_letter',
            'evaluation_form',
        ])
        form = RenewalApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            application = form.save(commit=False)
            # Link authenticated student
            if request.user.is_authenticated and hasattr(request.user, 'student_profile'):
                application.user = request.user
            application.save()
            request.session['renewal_pk'] = application.pk
            # Persist student_id in session for reliable lookup
            tracked = request.session.get('tracked_student_ids', [])
            if application.student_id not in tracked:
                tracked.append(application.student_id)
            request.session['tracked_student_ids'] = tracked
            # Send confirmation email
            send_application_confirmation(application, app_type='renewal')
            request.session['submission_success'] = f'Your renewal application has been submitted successfully! A confirmation email has been sent to {application.email}. Please wait while your documents are being reviewed to determine your eligibility as a Student Assistant.'
            return redirect('home:home')
    else:
        form = RenewalApplicationForm()

    # Build available offices list with slot info for the template
    available_offices_list = []
    for office in Office.objects.filter(is_active=True).order_by('name'):
        filled_new = NewApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).count()
        filled_renewal = RenewalApplication.objects.filter(
            assigned_office=office.name,
            status__in=['office_assigned', 'approved'],
        ).count()
        filled = filled_new + filled_renewal
        available = max(0, office.total_slots - filled)
        available_offices_list.append({
            'id': office.pk,
            'name': office.name,
            'available': available,
        })

    return render(request, 'home/apply_renew.html', {
        'form': form,
        'available_offices': available_offices_list,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
    })


def check_student_id(request):
    """AJAX endpoint — check if a student_id already exists in the database."""
    student_id = request.GET.get('student_id', '').strip()
    if not student_id or not student_id.isdigit():
        return JsonResponse({'exists': False})

    # Look up in NewApplication (the most recent matching record)
    app = NewApplication.objects.filter(student_id=student_id).order_by('-submitted_at').first()

    if app:
        # Build a full name from separate fields
        full_name_parts = [app.first_name]
        if app.middle_initial:
            full_name_parts.append(app.middle_initial + '.')
        full_name_parts.append(app.last_name)
        if app.extension_name:
            full_name_parts.append(app.extension_name)
        full_name = ' '.join(full_name_parts)

        # Look up ActiveStudentAssistant for hours and supervisor
        sa = ActiveStudentAssistant.objects.filter(student_id=student_id).order_by('-created_at').first()
        hours_rendered = ''
        supervisor_name = ''
        if sa:
            hours_rendered = str(int(sa.total_hours)) if sa.total_hours else ''
            last_eval = sa.evaluations.select_related('evaluated_by').order_by('-evaluated_at').first()
            if last_eval and last_eval.evaluated_by:
                supervisor_name = last_eval.evaluated_by.get_full_name() or last_eval.evaluated_by.username

        return JsonResponse({
            'exists': True,
            'source': 'new',
            'data': {
                'full_name': full_name,
                'first_name': app.first_name,
                'middle_initial': app.middle_initial,
                'last_name': app.last_name,
                'extension_name': app.extension_name or '',
                'email': app.email,
                'contact_number': app.contact_number,
                'address': app.address,
                'course': app.course,
                'year_level': str(app.year_level),
                'semester': app.semester,
                'status': app.get_status_display(),
                'assigned_office': app.assigned_office or '',
                'hours_rendered': hours_rendered,
                'supervisor_name': supervisor_name,
            },
        })

    # Also check RenewalApplication
    renewal = RenewalApplication.objects.filter(student_id=student_id).order_by('-submitted_at').first()
    if renewal:
        # Look up ActiveStudentAssistant for hours and supervisor
        sa = ActiveStudentAssistant.objects.filter(student_id=student_id).order_by('-created_at').first()
        hours_rendered = str(renewal.hours_rendered) if renewal.hours_rendered else ''
        supervisor_name = renewal.supervisor_name or ''
        if sa:
            if sa.total_hours:
                hours_rendered = str(int(sa.total_hours))
            last_eval = sa.evaluations.select_related('evaluated_by').order_by('-evaluated_at').first()
            if last_eval and last_eval.evaluated_by:
                supervisor_name = last_eval.evaluated_by.get_full_name() or last_eval.evaluated_by.username

        return JsonResponse({
            'exists': True,
            'source': 'renewal',
            'data': {
                'full_name': renewal.full_name,
                'email': renewal.email,
                'contact_number': renewal.contact_number,
                'address': renewal.address,
                'course': renewal.course,
                'year_level': str(renewal.year_level),
                'semester': renewal.semester,
                'status': renewal.get_status_display(),
                'assigned_office': renewal.assigned_office or '',
                'previous_office': renewal.previous_office or '',
                'hours_rendered': hours_rendered,
                'supervisor_name': supervisor_name,
            },
        })

    return JsonResponse({'exists': False})


@require_POST
def process_camera_photo(request):
    """Receive a base64 webcam image, process with OpenCV (cv2), and save."""
    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        field_name = data.get('field', 'photo')

        # Strip the data URL prefix (e.g. "data:image/png;base64,")
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]

        # Decode base64 to bytes
        img_bytes = base64.b64decode(image_data)

        # Convert to numpy array and decode with OpenCV
        np_arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if img is None:
            return JsonResponse({'status': 'error', 'message': 'Invalid image data'}, status=400)

        # --- OpenCV processing ---
        # Auto-adjust brightness/contrast
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        img = cv2.merge([l, a, b])
        img = cv2.cvtColor(img, cv2.COLOR_LAB2BGR)

        # Light denoise
        img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)

        # Save processed image
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'camera_photos')
        os.makedirs(upload_dir, exist_ok=True)

        filename = f"{field_name}_{uuid.uuid4().hex[:8]}.png"
        filepath = os.path.join(upload_dir, filename)
        cv2.imwrite(filepath, img)

        return JsonResponse({
            'status': 'ok',
            'filename': filename,
            'path': f"{settings.MEDIA_URL}camera_photos/{filename}",
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@require_POST
def validate_document(request):
    """AJAX endpoint — validate an uploaded file with OpenCV checks.

    Checks performed:
      • File size (max 10 MB)
      • File type (PDF / JPG / PNG only)
      • For images marked as 'id_picture': face detection via Haar cascade
      • For all images: blur detection (Laplacian variance) & blank page detection
    Returns JSON with ``valid``, ``warnings`` list, and ``checks`` dict.
    """
    from .forms import MAX_FILE_SIZE_MB, ALLOWED_DOC_EXTENSIONS, ALLOWED_IMAGE_EXTENSIONS

    uploaded = request.FILES.get('file')
    field_name = request.POST.get('field', '')

    if not uploaded:
        return JsonResponse({'valid': False, 'warnings': ['No file uploaded.'], 'checks': {}}, status=400)

    warnings = []
    checks = {}

    # ── Size check ──
    size_mb = uploaded.size / (1024 * 1024)
    checks['size_mb'] = round(size_mb, 2)
    if size_mb > MAX_FILE_SIZE_MB:
        warnings.append(f'File is too large ({size_mb:.1f} MB). Maximum is {MAX_FILE_SIZE_MB} MB.')
        checks['size_ok'] = False
    else:
        checks['size_ok'] = True

    # ── Type check ──
    ext = ''
    if '.' in uploaded.name:
        ext = ('.' + uploaded.name.rsplit('.', 1)[-1]).lower()
    is_image = ext in ('.jpg', '.jpeg', '.png')
    is_pdf = ext == '.pdf'
    allowed = ALLOWED_IMAGE_EXTENSIONS if field_name == 'id_picture' else ALLOWED_DOC_EXTENSIONS
    checks['extension'] = ext
    if ext not in allowed:
        warnings.append(f'File type "{ext}" is not allowed. Accepted: {", ".join(allowed)}.')
        checks['type_ok'] = False
    else:
        checks['type_ok'] = True

    # ── OpenCV image analysis (only for images) ──
    if is_image and checks.get('type_ok', True) and checks.get('size_ok', True):
        try:
            file_bytes = uploaded.read()
            uploaded.seek(0)
            np_arr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if img is None:
                warnings.append('Could not decode image. The file may be corrupted.')
                checks['decodable'] = False
            else:
                checks['decodable'] = True
                h, w = img.shape[:2]
                checks['resolution'] = f'{w}x{h}'

                # ── Blur detection (Laplacian variance) ──
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
                checks['blur_score'] = round(laplacian_var, 2)
                BLUR_THRESHOLD = 50.0
                if laplacian_var < BLUR_THRESHOLD:
                    warnings.append(
                        f'Image appears blurry (sharpness score: {laplacian_var:.0f}, '
                        f'minimum recommended: {BLUR_THRESHOLD:.0f}). '
                        'Please upload a clearer photo.'
                    )
                    checks['blur_ok'] = False
                else:
                    checks['blur_ok'] = True

                # ── Blank page detection (low std-dev = mostly uniform) ──
                std_dev = gray.std()
                checks['contrast_score'] = round(std_dev, 2)
                BLANK_THRESHOLD = 15.0
                if std_dev < BLANK_THRESHOLD:
                    warnings.append(
                        'Image appears to be blank or nearly blank. '
                        'Please upload the correct document.'
                    )
                    checks['blank_ok'] = False
                else:
                    checks['blank_ok'] = True

                # ── Face detection for id_picture ──
                if field_name == 'id_picture':
                    try:
                        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
                        face_cascade = cv2.CascadeClassifier(cascade_path)
                        faces = face_cascade.detectMultiScale(
                            gray,
                            scaleFactor=1.1,
                            minNeighbors=5,
                            minSize=(30, 30),
                        )
                        num_faces = len(faces) if faces is not None else 0
                        checks['faces_detected'] = num_faces
                        if num_faces == 0:
                            warnings.append(
                                'No face detected in the ID photo. '
                                'Please upload a clear, front-facing photo.'
                            )
                            checks['face_ok'] = False
                        elif num_faces > 1:
                            warnings.append(
                                f'{num_faces} faces detected. The ID photo should contain exactly one face.'
                            )
                            checks['face_ok'] = False
                        else:
                            checks['face_ok'] = True
                    except Exception:
                        checks['face_ok'] = None  # cascade not available

        except Exception as e:
            warnings.append(f'Image analysis error: {str(e)}')

    is_valid = len(warnings) == 0
    return JsonResponse({'valid': is_valid, 'warnings': warnings, 'checks': checks})


def staff_login(request):
    """Login page for staff users."""
    if request.user.is_authenticated:
        return redirect('home:staff_dashboard')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and (user.is_staff or user.is_superuser):
            login(request, user)
            return redirect('home:staff_dashboard')
        elif user is not None:
            error = 'This account does not have staff privileges.'
        else:
            error = 'Invalid username or password. Please try again.'

    return render(request, 'staff/login.html', {'error': error})


def director_login(request):
    """Login page for the Student Director (superuser)."""
    if request.user.is_authenticated:
        return redirect('home:director_dashboard')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_superuser:
            login(request, user)
            return redirect('home:director_dashboard')
        elif user is not None:
            error = 'This account does not have director privileges.'
        else:
            error = 'Invalid username or password. Please try again.'

    return render(request, 'director/login.html', {'error': error})


@login_required
def staff_dashboard(request):
    """Staff dashboard view. Accessible by staff users and superusers (director)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    # Auto-expire SAs whose duty period has ended
    auto_expire_student_assistants()

    # ── Real application data from NewApplication + RenewalApplication ──
    new_apps = NewApplication.objects.all()
    renewal_apps = RenewalApplication.objects.all()

    total_new = new_apps.count()
    total_renewal = renewal_apps.count()
    total_applications = total_new + total_renewal

    pending_count = (
        new_apps.filter(status='pending').count()
        + renewal_apps.filter(status='pending').count()
    )
    under_review_count = (
        new_apps.filter(status='under_review').count()
        + renewal_apps.filter(status='under_review').count()
    )
    interview_count = (
        new_apps.filter(status__in=['interview_scheduled', 'interview_done']).count()
        + renewal_apps.filter(status__in=['interview_scheduled', 'interview_done']).count()
    )
    office_assigned_count = (
        new_apps.filter(status='office_assigned').count()
        + renewal_apps.filter(status='office_assigned').count()
    )
    approved_count = (
        new_apps.filter(status='approved').count()
        + renewal_apps.filter(status='approved').count()
    )
    rejected_count = (
        new_apps.filter(status='rejected').count()
        + renewal_apps.filter(status='rejected').count()
    )

    stats = {
        'total_applications': total_applications,
        'pending_review': pending_count + under_review_count,
        'interview_scheduled': interview_count,
        'office_assigned': office_assigned_count,
        'approved': approved_count,
        'rejected': rejected_count,
    }

    # ── Build unified list of ALL students (new + renewal) ──
    all_students = []
    today = _date.today()

    for app in new_apps.order_by('-submitted_at'):
        dob = app.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)) if dob else None
        all_students.append({
            'pk': app.pk,
            'app_type': 'New',
            'app_type_class': 'new',
            'student_id': app.student_id,
            'first_name': app.first_name,
            'last_name': app.last_name,
            'middle_initial': app.middle_initial,
            'extension_name': app.extension_name,
            'full_name': f"{app.first_name} {app.middle_initial}. {app.last_name}" + (f" {app.extension_name}" if app.extension_name else ""),
            'email': app.email,
            'contact_number': app.contact_number,
            'address': app.address,
            'gender_display': app.get_gender_display(),
            'date_of_birth': app.date_of_birth,
            'age': age,
            'course': app.course,
            'year_level_display': app.get_year_level_display(),
            'semester_display': app.get_semester_display(),
            'preferred_office': app.preferred_office.name if app.preferred_office else '',
            'available_days': ' '.join(sorted(app.availability_schedule.keys())) if app.availability_schedule else '',
            'interview_date': app.interview_date,
            'assigned_office': app.assigned_office,
            'start_date': app.start_date,
            'submitted_at': app.submitted_at,
            'status': app.status,
            'status_display': app.get_status_display(),
            'review_url_name': 'home:staff_review_application',
            'status_url_name': 'home:staff_update_application_status',
        })

    for app in renewal_apps.order_by('-submitted_at'):
        all_students.append({
            'pk': app.pk,
            'app_type': 'Renewal',
            'app_type_class': 'renewal',
            'student_id': app.student_id,
            'first_name': app.full_name.split()[0] if app.full_name else '',
            'last_name': ' '.join(app.full_name.split()[1:]) if app.full_name else '',
            'middle_initial': '',
            'extension_name': '',
            'full_name': app.full_name,
            'email': app.email,
            'contact_number': app.contact_number,
            'address': app.address,
            'gender_display': '',
            'date_of_birth': None,
            'age': '',
            'course': app.course,
            'year_level_display': app.get_year_level_display(),
            'semester_display': app.get_semester_display(),
            'preferred_office': app.preferred_office.name if app.preferred_office else '',
            'available_days': ' '.join(sorted(app.availability_schedule.keys())) if app.availability_schedule else '',
            'interview_date': app.interview_date,
            'assigned_office': app.assigned_office,
            'start_date': app.start_date,
            'submitted_at': app.submitted_at,
            'status': app.status,
            'status_display': app.get_status_display(),
            'review_url_name': 'home:staff_review_application',
            'status_url_name': 'home:staff_update_application_status',
            'is_renewal': True,
        })

    # Sort all by submitted_at descending
    all_students.sort(key=lambda x: x['submitted_at'], reverse=True)

    # Applications needing attention (pending + under_review), newest first
    pending_applications = new_apps.filter(
        status__in=['pending', 'under_review']
    ).order_by('-submitted_at')

    # All applications for the full table (keeping for backward compat)
    all_applications = new_apps.order_by('-submitted_at')

    # Recent activity: last 10 approved/rejected with timestamps (both types)
    recent_new = list(new_apps.filter(status__in=['approved', 'rejected']).order_by('-submitted_at')[:10])
    recent_renewal = list(renewal_apps.filter(status__in=['approved', 'rejected']).order_by('-submitted_at')[:10])

    # Normalize renewal apps to have first_name/last_name for template
    for r in recent_renewal:
        r.first_name = r.full_name.split()[0] if r.full_name else ''
        r.last_name = ' '.join(r.full_name.split()[1:]) if r.full_name else ''
        r.app_type = 'Renewal'

    for n in recent_new:
        n.app_type = 'New'

    recent_combined = recent_new + recent_renewal
    recent_combined.sort(key=lambda x: x.submitted_at, reverse=True)
    recent_activity = recent_combined[:10]

    from django.db.models import Q as _Q
    context = {
        'staff_name': request.user.get_full_name() or request.user.username,
        'pending_applications': pending_applications,
        'all_applications': all_applications,
        'all_students': all_students,
        'recent_activity': recent_activity,
        'stats': stats,
        # Management data (active / non-expired)
        'reminders': Reminder.objects.filter(
            _Q(expires_at__isnull=True) | _Q(expires_at__gte=_date.today())
        ).order_by('-created_at'),
        'upcoming_dates': UpcomingDate.objects.filter(
            _Q(expires_at__isnull=True) | _Q(expires_at__gte=_date.today())
        ).order_by('date'),
        'announcements': Announcement.objects.filter(
            _Q(expires_at__isnull=True) | _Q(expires_at__gte=_date.today())
        ).order_by('-published_at'),
        # Expired content
        'expired_reminders': Reminder.objects.filter(
            expires_at__isnull=False, expires_at__lt=_date.today()
        ).order_by('-expires_at'),
        'expired_dates': UpcomingDate.objects.filter(
            expires_at__isnull=False, expires_at__lt=_date.today()
        ).order_by('-expires_at'),
        'expired_announcements': Announcement.objects.filter(
            expires_at__isnull=False, expires_at__lt=_date.today()
        ).order_by('-expires_at'),
        # Forms
        'reminder_form': ReminderForm(),
        'date_form': UpcomingDateForm(),
        'announcement_form': AnnouncementForm(),
    }
    return render(request, 'staff/dashboard.html', context)


@login_required
def staff_review_application(request, pk):
    """View full details of a single application."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)

    # Build document list with status for this application
    doc_fields = [
        ('application_form', 'Application Form'),
        ('id_picture', '2x2 ID Picture'),
        ('barangay_clearance', 'Barangay Clearance'),
        ('parents_itr', "Parent's ITR / Certificate of Indigency"),
        ('enrolment_form', 'Certificate of Enrolment'),
        ('schedule_classes', 'Schedule of Classes'),
        ('proof_insurance', 'Proof of Insurance'),
        ('grades_last_sem', 'Grades Last Semester'),
        ('official_time', 'Official Time'),
    ]
    documents = []
    for field_name, label in doc_fields:
        # Official Time = availability schedule, not a file upload
        if field_name == 'official_time':
            sched = app.availability_schedule
            has_schedule = bool(sched and isinstance(sched, dict) and len(sched) > 0)
            doc_entry = {
                'name': label,
                'field': field_name,
                'file': None,
                'uploaded': has_schedule,
                'validation': None,
                'returned_reason': (app.returned_documents or {}).get(field_name, ''),
                'is_schedule': True,
            }
            documents.append(doc_entry)
            continue
        file_field = getattr(app, field_name)
        doc_entry = {
            'name': label,
            'field': field_name,
            'file': file_field if file_field else None,
            'uploaded': bool(file_field),
            'validation': None,
            'returned_reason': (app.returned_documents or {}).get(field_name, ''),
        }
        if file_field:
            doc_entry['validation'] = _validate_uploaded_file(file_field, field_name)
        documents.append(doc_entry)

    total_docs = len(documents)
    uploaded_docs = sum(1 for d in documents if d['uploaded'])

    # Calculate age from date of birth
    today = _date.today()
    dob = app.date_of_birth
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    availability = app.availability_schedule or {}
    schedule_grid = []
    for ts_val, ts_label in TIME_SLOT_CHOICES:
        row = {'label': ts_label, 'cells': []}
        for d_val, _d_label in DAY_CHOICES:
            row['cells'].append(d_val in availability and ts_val in availability.get(d_val, []))
        schedule_grid.append(row)

    context = {
        'app': app,
        'age': age,
        'documents': documents,
        'total_docs': total_docs,
        'uploaded_docs': uploaded_docs,
        'staff_name': request.user.get_full_name() or request.user.username,
        'availability': availability,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
        'schedule_grid': schedule_grid,
        'notes_log': app.notes_log.exclude(note_type='status_change'),
    }
    return render(request, 'staff/review_application.html', context)


@login_required
@require_POST
def staff_update_application_status(request, pk):
    """Update the status of an application, optionally with scheduling data."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    new_status = request.POST.get('status')
    if new_status in dict(NewApplication.STATUS_CHOICES):
        old_status = app.status
        app.status = new_status

        # Handle interview scheduling
        if new_status == 'interview_scheduled':
            interview_dt = request.POST.get('interview_date')
            if interview_dt:
                from datetime import datetime as _dt
                try:
                    app.interview_date = _dt.fromisoformat(interview_dt)
                except (ValueError, TypeError):
                    pass

        # Handle office assignment — auto-fill from preferred_office if not
        # explicitly provided by staff
        if new_status == 'office_assigned':
            office = request.POST.get('assigned_office', '').strip()
            if office:
                app.assigned_office = office
            elif app.preferred_office:
                app.assigned_office = app.preferred_office.name

        # Handle final approval with start date — auto-assign office from preference
        if new_status == 'approved':
            start = request.POST.get('start_date')
            if start:
                app.start_date = _date.fromisoformat(start)
            # Always assign from the student's preferred office
            if app.preferred_office:
                app.assigned_office = app.preferred_office.name

        # Handle schedule mismatch
        if new_status == 'schedule_mismatch':
            mismatch_note = request.POST.get('schedule_mismatch_note', '').strip().title()
            app.schedule_mismatch_note = mismatch_note
            app.schedule_verified = False
            send_schedule_mismatch_email(app, mismatch_note)
            ApplicationNote.objects.create(
                new_application=app, author=request.user,
                note_type='schedule_mismatch',
                content=f'Schedule mismatch flagged: {mismatch_note}',
            )

        # Handle document request
        if new_status == 'documents_requested':
            docs_note = request.POST.get('requested_documents_note', '').strip().title()
            app.requested_documents_note = docs_note
            send_document_request_email(app, docs_note)
            ApplicationNote.objects.create(
                new_application=app, author=request.user,
                note_type='document_request',
                content=f'Documents requested: {docs_note}',
            )

        app.save()

        # Send status email for all other transitions
        if new_status not in ('schedule_mismatch', 'documents_requested'):
            extra = ''
            if new_status == 'interview_scheduled' and app.interview_date:
                extra = f'Interview date: {app.interview_date.strftime("%B %d, %Y — %I:%M %p")}'
            send_status_update_email(app, old_status, new_status, extra)

        # Auto-create ActiveStudentAssistant record on approval
        if new_status == 'approved':
            _create_active_sa_from_application(app)

    next_url = request.POST.get('next', '')
    if next_url:
        return redirect(next_url)
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_return_document(request, pk):
    """Staff returns a specific document with a reason."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    field_name = request.POST.get('field_name', '').strip()
    reason = request.POST.get('reason', '').strip().title()
    doc_label = request.POST.get('doc_label', field_name)

    # Validate field_name against known document fields
    valid_fields = [
        'application_form', 'id_picture', 'barangay_clearance', 'parents_itr',
        'enrolment_form', 'schedule_classes', 'proof_insurance', 'grades_last_sem',
        'official_time',
    ]
    if field_name not in valid_fields or not reason:
        messages.error(request, 'Invalid document return request.')
        return redirect('home:staff_review_application', pk=pk)

    # Update returned_documents JSONField
    returned = app.returned_documents or {}
    returned[field_name] = reason
    app.returned_documents = returned
    old_status = app.status
    app.status = 'documents_requested'
    app.save()

    ApplicationNote.objects.create(
        new_application=app, author=request.user,
        note_type='document_return',
        content=f'Document returned — {doc_label}: {reason}',
    )

    # Build per-document summary for email
    doc_summary = '\n'.join(f'  • {k}: {v}' for k, v in returned.items())
    send_document_request_email(app, f'The following document(s) need to be re-uploaded:\n{doc_summary}')

    messages.success(request, f'"{doc_label}" has been returned to the student.')
    return redirect('home:staff_review_application', pk=pk)


# ================================================================
#  STAFF CRUD — Reminders
# ================================================================

@login_required
@require_POST
def staff_add_reminder(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    form = ReminderForm(request.POST)
    if form.is_valid():
        form.save()
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_edit_reminder(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    reminder = get_object_or_404(Reminder, pk=pk)
    form = ReminderForm(request.POST, instance=reminder)
    if form.is_valid():
        form.save()
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_delete_reminder(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    get_object_or_404(Reminder, pk=pk).delete()
    return redirect('home:staff_dashboard')


# ================================================================
#  STAFF CRUD — Upcoming Dates
# ================================================================

@login_required
@require_POST
def staff_add_date(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    form = UpcomingDateForm(request.POST)
    if form.is_valid():
        form.save()
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_edit_date(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    obj = get_object_or_404(UpcomingDate, pk=pk)
    form = UpcomingDateForm(request.POST, instance=obj)
    if form.is_valid():
        form.save()
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_delete_date(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    get_object_or_404(UpcomingDate, pk=pk).delete()
    return redirect('home:staff_dashboard')


# ================================================================
#  STAFF CRUD — Announcements
# ================================================================

@login_required
@require_POST
def staff_add_announcement(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    form = AnnouncementForm(request.POST, request.FILES)
    if form.is_valid():
        form.save()
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_edit_announcement(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    obj = get_object_or_404(Announcement, pk=pk)
    form = AnnouncementForm(request.POST, request.FILES, instance=obj)
    if form.is_valid():
        form.save()
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_delete_announcement(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    get_object_or_404(Announcement, pk=pk).delete()
    return redirect('home:staff_dashboard')


@login_required
def director_dashboard(request):
    """Student Director dashboard view. Accessible by superusers only."""
    if not request.user.is_superuser:
        return redirect('home:home')

    # Auto-expire SAs whose duty period has ended
    auto_expire_student_assistants()

    all_apps = NewApplication.objects.all()
    renewal_apps = RenewalApplication.objects.all()

    # Applications awaiting interview (interview_scheduled)
    interview_apps = all_apps.filter(
        status='interview_scheduled'
    ).order_by('interview_date')

    # Applications where interview is done, ready for office assignment
    interview_done_apps = all_apps.filter(
        status='interview_done'
    ).order_by('-submitted_at')

    # Applications that have been assigned an office but not yet approved
    office_assigned_apps = all_apps.filter(
        status='office_assigned'
    ).order_by('-submitted_at')

    # Approved student assistants
    approved_apps = all_apps.filter(status='approved').order_by('-submitted_at')

    # Stats
    stats = {
        'total_applications': all_apps.count(),
        'awaiting_interview': interview_apps.count(),
        'interview_done': interview_done_apps.count(),
        'office_assigned': office_assigned_apps.count(),
        'approved': approved_apps.count(),
        'rejected': all_apps.filter(status='rejected').count(),
    }

    offices = Office.objects.filter(is_active=True).order_by('name')

    # ── Build unified list of ALL students for the director table ──
    all_students = []
    today = _date.today()

    for app in all_apps.order_by('-submitted_at'):
        dob = app.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)) if dob else None
        all_students.append({
            'pk': app.pk,
            'app_type': 'New',
            'app_type_class': 'new',
            'student_id': app.student_id,
            'first_name': app.first_name,
            'last_name': app.last_name,
            'full_name': f"{app.first_name} {app.middle_initial}. {app.last_name}" + (f" {app.extension_name}" if app.extension_name else ""),
            'email': app.email,
            'contact_number': app.contact_number,
            'course': app.course,
            'year_level_display': app.get_year_level_display(),
            'semester_display': app.get_semester_display(),
            'preferred_office': app.preferred_office.name if app.preferred_office else '',
            'available_days': ' '.join(sorted(app.availability_schedule.keys())) if app.availability_schedule else '',
            'interview_date': app.interview_date,
            'assigned_office': app.assigned_office,
            'submitted_at': app.submitted_at,
            'status': app.status,
            'status_display': app.get_status_display(),
            'is_renewal': False,
        })

    for app in renewal_apps.order_by('-submitted_at'):
        all_students.append({
            'pk': app.pk,
            'app_type': 'Renewal',
            'app_type_class': 'renewal',
            'student_id': app.student_id,
            'full_name': app.full_name,
            'first_name': app.full_name.split()[0] if app.full_name else '',
            'last_name': ' '.join(app.full_name.split()[1:]) if app.full_name else '',
            'email': app.email,
            'contact_number': app.contact_number,
            'course': app.course,
            'year_level_display': app.get_year_level_display(),
            'semester_display': app.get_semester_display(),
            'preferred_office': app.preferred_office.name if app.preferred_office else '',
            'available_days': ' '.join(sorted(app.availability_schedule.keys())) if app.availability_schedule else '',
            'interview_date': app.interview_date,
            'assigned_office': app.assigned_office,
            'submitted_at': app.submitted_at,
            'status': app.status,
            'status_display': app.get_status_display(),
            'is_renewal': True,
        })

    all_students.sort(key=lambda x: x['submitted_at'], reverse=True)

    context = {
        'director_name': request.user.get_full_name() or 'Director',
        'interview_apps': interview_apps,
        'interview_done_apps': interview_done_apps,
        'office_assigned_apps': office_assigned_apps,
        'approved_apps': approved_apps,
        'all_apps': all_apps.order_by('-submitted_at'),
        'all_students': all_students,
        'stats': stats,
        'offices': offices,
    }
    return render(request, 'director/dashboard.html', context)


@login_required
def director_review_application(request, pk):
    """Director's view of a single application — read-only review with director actions."""
    if not request.user.is_superuser:
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)

    doc_fields = [
        ('application_form', 'Application Form'),
        ('id_picture', '2x2 ID Picture'),
        ('barangay_clearance', 'Barangay Clearance'),
        ('parents_itr', "Parent's ITR / Certificate of Indigency"),
        ('enrolment_form', 'Certificate of Enrolment'),
        ('schedule_classes', 'Schedule of Classes'),
        ('proof_insurance', 'Proof of Insurance'),
        ('grades_last_sem', 'Grades Last Semester'),
        ('official_time', 'Official Time'),
    ]
    documents = []
    for field_name, label in doc_fields:
        if field_name == 'official_time':
            sched = app.availability_schedule
            has_schedule = bool(sched and isinstance(sched, dict) and len(sched) > 0)
            doc_entry = {
                'name': label,
                'field': field_name,
                'file': None,
                'uploaded': has_schedule,
                'validation': None,
                'returned_reason': (app.returned_documents or {}).get(field_name, ''),
                'is_schedule': True,
            }
            documents.append(doc_entry)
            continue
        file_field = getattr(app, field_name)
        doc_entry = {
            'name': label,
            'field': field_name,
            'file': file_field if file_field else None,
            'uploaded': bool(file_field),
            'validation': None,
            'returned_reason': (app.returned_documents or {}).get(field_name, ''),
        }
        if file_field:
            doc_entry['validation'] = _validate_uploaded_file(file_field, field_name)
        documents.append(doc_entry)

    total_docs = len(documents)
    uploaded_docs = sum(1 for d in documents if d['uploaded'])

    offices = Office.objects.filter(is_active=True).order_by('name')

    # Calculate age from date of birth
    today = _date.today()
    dob = app.date_of_birth
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    # Availability schedule
    availability = app.availability_schedule or {}
    schedule_grid = []
    for ts_val, ts_label in TIME_SLOT_CHOICES:
        row = {'label': ts_label, 'cells': []}
        for d_val, _d_label in DAY_CHOICES:
            row['cells'].append(d_val in availability and ts_val in availability.get(d_val, []))
        schedule_grid.append(row)

    # Notes log (exclude auto-generated status-change entries)
    notes_log = ApplicationNote.objects.filter(
        new_application=app
    ).exclude(note_type='status_change').select_related('author').order_by('-created_at')

    context = {
        'app': app,
        'age': age,
        'documents': documents,
        'total_docs': total_docs,
        'uploaded_docs': uploaded_docs,
        'director_name': request.user.get_full_name() or 'Director',
        'offices': offices,
        'availability': availability,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
        'schedule_grid': schedule_grid,
        'notes_log': notes_log,
    }
    return render(request, 'director/review_application.html', context)


@login_required
@require_POST
def director_update_application_status(request, pk):
    """Director-specific status update (office assignment, approval, etc.)."""
    if not request.user.is_superuser:
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    new_status = request.POST.get('status')
    if new_status in dict(NewApplication.STATUS_CHOICES):
        old_status = app.status
        app.status = new_status

        # Handle office assignment — auto-fill from preferred_office
        if new_status == 'office_assigned':
            office = request.POST.get('assigned_office', '').strip()
            if office:
                app.assigned_office = office
            elif app.preferred_office:
                app.assigned_office = app.preferred_office.name

        # Handle final approval — auto-assign from preferred_office
        if new_status == 'approved':
            start = request.POST.get('start_date')
            if start:
                app.start_date = _date.fromisoformat(start)
            # Always assign from the student's preferred office
            if app.preferred_office:
                app.assigned_office = app.preferred_office.name

        if new_status == 'interview_scheduled':
            interview_dt = request.POST.get('interview_date')
            if interview_dt:
                from datetime import datetime as _dt
                try:
                    app.interview_date = _dt.fromisoformat(interview_dt)
                except (ValueError, TypeError):
                    pass

        # Handle schedule mismatch
        if new_status == 'schedule_mismatch':
            mismatch_note = request.POST.get('schedule_mismatch_note', '').strip().title()
            app.schedule_mismatch_note = mismatch_note
            app.schedule_verified = False
            send_schedule_mismatch_email(app, mismatch_note)
            ApplicationNote.objects.create(
                new_application=app, author=request.user,
                note_type='schedule_mismatch',
                content=f'Schedule mismatch flagged: {mismatch_note}',
            )

        # Handle document request
        if new_status == 'documents_requested':
            docs_note = request.POST.get('requested_documents_note', '').strip().title()
            app.requested_documents_note = docs_note
            send_document_request_email(app, docs_note)
            ApplicationNote.objects.create(
                new_application=app, author=request.user,
                note_type='document_request',
                content=f'Documents requested: {docs_note}',
            )

        app.save()

        # Send status email for all other transitions
        if new_status not in ('schedule_mismatch', 'documents_requested'):
            extra = ''
            if new_status == 'interview_scheduled' and app.interview_date:
                extra = f'Interview date: {app.interview_date.strftime("%B %d, %Y — %I:%M %p")}'
            send_status_update_email(app, old_status, new_status, extra)

        # Auto-create ActiveStudentAssistant record on approval
        if new_status == 'approved':
            _create_active_sa_from_application(app)

    next_url = request.POST.get('next', '')
    if next_url:
        return redirect(next_url)
    return redirect('home:director_dashboard')


@login_required
@require_POST
def director_return_document(request, pk):
    """Director returns a specific document with a reason."""
    if not request.user.is_superuser:
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    field_name = request.POST.get('field_name', '').strip()
    reason = request.POST.get('reason', '').strip().title()
    doc_label = request.POST.get('doc_label', field_name)

    valid_fields = [
        'application_form', 'id_picture', 'barangay_clearance', 'parents_itr',
        'enrolment_form', 'schedule_classes', 'proof_insurance', 'grades_last_sem',
        'official_time',
    ]
    if field_name not in valid_fields or not reason:
        messages.error(request, 'Invalid document return request.')
        return redirect('home:director_review_application', pk=pk)

    returned = app.returned_documents or {}
    returned[field_name] = reason
    app.returned_documents = returned
    old_status = app.status
    app.status = 'documents_requested'
    app.save()

    ApplicationNote.objects.create(
        new_application=app, author=request.user,
        note_type='document_return',
        content=f'Document returned — {doc_label}: {reason}',
    )

    doc_summary = '\n'.join(f'  • {k}: {v}' for k, v in returned.items())
    send_document_request_email(app, f'The following document(s) need to be re-uploaded:\n{doc_summary}')

    messages.success(request, f'"{doc_label}" has been returned to the student.')
    return redirect('home:director_review_application', pk=pk)


# ================================================================
#  STAFF CRUD — Offices
# ================================================================

@login_required
@require_POST
def staff_add_office(request):
    """Create a new office. Staff only."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    form = OfficeForm(request.POST)
    if form.is_valid():
        office = form.save()
        messages.success(request, f'Office "{office.name}" has been created successfully!')
    else:
        error_list = '; '.join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )
        messages.error(request, f'Failed to create office: {error_list}')
    return redirect('home:available_offices')


@login_required
@require_POST
def staff_edit_office(request, pk):
    """Edit an existing office. Staff only."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    office = get_object_or_404(Office, pk=pk)
    form = OfficeForm(request.POST, instance=office)
    if form.is_valid():
        form.save()
        messages.success(request, f'Office "{office.name}" has been updated successfully!')
    else:
        error_list = '; '.join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )
        messages.error(request, f'Failed to update office: {error_list}')
    return redirect('home:available_offices')


@login_required
@require_POST
def staff_delete_office(request, pk):
    """Deactivate (soft-delete) an office. Staff only."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    office = get_object_or_404(Office, pk=pk)
    office_name = office.name
    office.is_active = False
    office.save()
    messages.success(request, f'Office "{office_name}" has been deactivated.')
    return redirect('home:available_offices')


@login_required
@require_POST
def director_move_office(request, pk):
    """Update office coordinates via AJAX. Director (superuser) only."""
    if not request.user.is_superuser:
        return JsonResponse({'error': 'forbidden'}, status=403)
    office = get_object_or_404(Office, pk=pk)
    try:
        import json as _json
        data = _json.loads(request.body)
        lat = float(data['lat'])
        lng = float(data['lng'])
    except (KeyError, ValueError, _json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid coordinates'}, status=400)
    office.latitude = lat
    office.longitude = lng
    office.save(update_fields=['latitude', 'longitude'])
    return JsonResponse({'success': True, 'name': office.name, 'lat': lat, 'lng': lng})


@login_required
def staff_get_office_json(request, pk):
    """Return a single office as JSON (for populating edit forms)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'forbidden'}, status=403)
    office = get_object_or_404(Office, pk=pk)
    return JsonResponse({
        'id': office.pk,
        'name': office.name,
        'building': office.building,
        'room': office.room,
        'hours': office.hours,
        'head': office.head,
        'total_slots': office.total_slots,
        'latitude': office.latitude,
        'longitude': office.longitude,
        'icon': office.icon,
        'description': office.description,
        'is_active': office.is_active,
    })


# ================================================================
#  ACTIVE SA MANAGEMENT — Staff Views
# ================================================================

@login_required
def staff_active_sa_list(request):
    """List all active student assistants for staff management."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    active_sas = ActiveStudentAssistant.objects.select_related(
        'assigned_office', 'new_application', 'renewal_application',
    ).all()

    status_filter = request.GET.get('status', '')
    office_filter = request.GET.get('office', '')
    search_q = request.GET.get('q', '')

    if status_filter:
        active_sas = active_sas.filter(status=status_filter)
    if office_filter:
        active_sas = active_sas.filter(assigned_office__pk=office_filter)
    if search_q:
        from django.db.models import Q
        active_sas = active_sas.filter(
            Q(full_name__icontains=search_q) |
            Q(student_id__icontains=search_q) |
            Q(email__icontains=search_q)
        )

    offices = Office.objects.filter(is_active=True).order_by('name')

    # Stats
    total = ActiveStudentAssistant.objects.count()
    active_count = ActiveStudentAssistant.objects.filter(status='active').count()
    completed_count = ActiveStudentAssistant.objects.filter(status='completed').count()
    expired_count = ActiveStudentAssistant.objects.filter(status='expired').count()

    context = {
        'active_sas': active_sas,
        'offices': offices,
        'stats': {
            'total': total,
            'active': active_count,
            'completed': completed_count,
            'expired': expired_count,
        },
        'current_status': status_filter,
        'current_office': office_filter,
        'search_q': search_q,
        'staff_name': request.user.get_full_name() or request.user.username,
    }
    return render(request, 'staff/active_sa_list.html', context)


@login_required
def staff_sa_detail(request, pk):
    """View details of a single active SA — attendance history, evaluations, etc."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    sa = get_object_or_404(
        ActiveStudentAssistant.objects.select_related('assigned_office'),
        pk=pk,
    )
    attendance = sa.attendance_records.all()
    evaluations = sa.evaluations.all()

    # Attendance summary
    from django.db.models import Sum, Count
    from decimal import Decimal
    total_days = attendance.count()
    present_days = attendance.filter(status='present').count()
    late_days = attendance.filter(status='late').count()
    absent_days = attendance.filter(status='absent').count()
    excused_days = attendance.filter(status='excused').count()

    # Recalculate total hours from records
    total_hours = Decimal('0')
    for rec in attendance:
        total_hours += Decimal(str(rec.hours_worked))
    # Update cached total_hours
    if sa.total_hours != total_hours:
        sa.total_hours = total_hours
        sa.save(update_fields=['total_hours'])

    attendance_form = AttendanceForm(initial={'date': _date.today()})
    evaluation_form = PerformanceEvaluationForm()
    status_form = ActiveSAStatusForm(instance=sa)

    # ── Monthly attendance breakdown ──
    monthly_breakdown = []
    if sa.start_date:
        from collections import defaultdict
        hours_by_month = defaultdict(Decimal)
        status_by_month = defaultdict(lambda: {'present': 0, 'late': 0, 'absent': 0})
        for rec in attendance:
            key = (rec.date.year, rec.date.month)
            if rec.time_in and rec.time_out:
                hours_by_month[key] += Decimal(str(rec.hours_worked))
            if rec.status in ('present', 'late', 'absent'):
                status_by_month[key][rec.status] += 1
        HOURLY_RATE = Decimal('35.00')
        for i in range(4):
            m = sa.start_date.month + i
            y = sa.start_date.year
            if m > 12:
                m -= 12
                y += 1
            month_label = _date(y, m, 1).strftime('%B %Y')
            hours = hours_by_month.get((y, m), Decimal('0'))
            counts = status_by_month.get((y, m), {'present': 0, 'late': 0, 'absent': 0})
            monthly_breakdown.append({
                'month': month_label,
                'hours': float(hours),
                'payout': float(round(hours * HOURLY_RATE, 2)),
                'present': counts['present'],
                'late': counts['late'],
                'absent': counts['absent'],
            })

    # ── Weekly summary ──
    weekly_summary = _build_weekly_summary(attendance, sa.start_date)

    # ── Semester report ──
    semester_report = _build_semester_report(sa)

    # ── Alerts ──
    consec_count, consec_dates = _check_consecutive_absences(sa)
    consecutive_absence_alert = None
    if consec_count >= CONSECUTIVE_ABSENCE_THRESHOLD:
        consecutive_absence_alert = {'count': consec_count, 'dates': consec_dates, 'threshold': CONSECUTIVE_ABSENCE_THRESHOLD}
    late_count, late_month = _check_late_threshold(sa)
    late_threshold_alert = None
    if late_count >= LATE_MONTHLY_THRESHOLD:
        late_threshold_alert = {'count': late_count, 'month': late_month, 'threshold': LATE_MONTHLY_THRESHOLD}

    context = {
        'sa': sa,
        'attendance': attendance[:30],  # Last 30 records
        'evaluations': evaluations,
        'attendance_summary': {
            'total_days': total_days,
            'present': present_days,
            'late': late_days,
            'absent': absent_days,
            'excused': excused_days,
        },
        'attendance_form': attendance_form,
        'evaluation_form': evaluation_form,
        'status_form': status_form,
        'staff_name': request.user.get_full_name() or request.user.username,
        'monthly_breakdown': monthly_breakdown,
        'weekly_summary': weekly_summary,
        'semester_report': semester_report,
        'consecutive_absence_alert': consecutive_absence_alert,
        'late_threshold_alert': late_threshold_alert,
    }
    return render(request, 'staff/sa_detail.html', context)


@login_required
@require_POST
def staff_log_attendance(request, pk):
    """Log an attendance record for an active SA."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    sa = get_object_or_404(ActiveStudentAssistant, pk=pk)
    form = AttendanceForm(request.POST)
    if form.is_valid():
        record = form.save(commit=False)
        att_date = record.date

        # ── Attendance blocking checks ──
        # Weekend check
        if att_date.weekday() >= 5:
            messages.error(request, f'Cannot log attendance on a weekend ({att_date.strftime("%A")}).')
            return redirect('home:staff_sa_detail', pk=pk)

        # Expired duty check
        if sa.end_date and att_date > sa.end_date:
            messages.error(request, f'Cannot log attendance past the duty end date ({sa.end_date}).')
            return redirect('home:staff_sa_detail', pk=pk)

        # No-duty day check
        from django.db.models import Q as _Q
        is_no_duty = NoDutyDay.objects.filter(
            _Q(office=sa.assigned_office) | _Q(office__isnull=True),
            date=att_date,
        ).exists()
        if is_no_duty:
            messages.error(request, f'{att_date} is a No-Duty Day. Attendance cannot be logged.')
            return redirect('home:staff_sa_detail', pk=pk)

        record.student_assistant = sa
        record.logged_by = request.user
        record.save()

        # Update cached total_hours
        from decimal import Decimal
        sa.total_hours += Decimal(str(record.hours_worked))
        sa.save(update_fields=['total_hours'])

        messages.success(request, f'Attendance logged for {sa.full_name}.')
    else:
        error_list = '; '.join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )
        messages.error(request, f'Failed to log attendance: {error_list}')

    return redirect('home:staff_sa_detail', pk=pk)


@login_required
@require_POST
def staff_delete_attendance(request, sa_pk, att_pk):
    """Delete an attendance record."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    record = get_object_or_404(AttendanceRecord, pk=att_pk, student_assistant__pk=sa_pk)
    sa = record.student_assistant

    # Subtract hours before deleting
    from decimal import Decimal
    sa.total_hours = max(Decimal('0'), sa.total_hours - Decimal(str(record.hours_worked)))
    sa.save(update_fields=['total_hours'])

    record.delete()
    messages.success(request, 'Attendance record deleted.')
    return redirect('home:staff_sa_detail', pk=sa_pk)


@login_required
@require_POST
def staff_update_sa_status(request, pk):
    """Update the status/settings of an active SA."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    sa = get_object_or_404(ActiveStudentAssistant, pk=pk)
    form = ActiveSAStatusForm(request.POST, instance=sa)
    if form.is_valid():
        form.save()
        messages.success(request, f'Status updated for {sa.full_name}.')
    else:
        messages.error(request, 'Failed to update SA status.')
    return redirect('home:staff_sa_detail', pk=pk)


# ================================================================
#  ACTIVE SA MANAGEMENT — Director Views
# ================================================================

@login_required
def director_sa_list(request):
    """Director's view of all active SAs."""
    if not request.user.is_superuser:
        return redirect('home:home')

    active_sas = ActiveStudentAssistant.objects.select_related(
        'assigned_office', 'new_application', 'renewal_application',
    ).all()

    status_filter = request.GET.get('status', '')
    office_filter = request.GET.get('office', '')
    search_q = request.GET.get('q', '')

    if status_filter:
        active_sas = active_sas.filter(status=status_filter)
    if office_filter:
        active_sas = active_sas.filter(assigned_office__pk=office_filter)
    if search_q:
        from django.db.models import Q
        active_sas = active_sas.filter(
            Q(full_name__icontains=search_q) |
            Q(student_id__icontains=search_q) |
            Q(email__icontains=search_q)
        )

    offices = Office.objects.filter(is_active=True).order_by('name')

    total = ActiveStudentAssistant.objects.count()
    active_count = ActiveStudentAssistant.objects.filter(status='active').count()
    completed_count = ActiveStudentAssistant.objects.filter(status='completed').count()
    expired_count = ActiveStudentAssistant.objects.filter(status='expired').count()

    context = {
        'active_sas': active_sas,
        'offices': offices,
        'stats': {
            'total': total,
            'active': active_count,
            'completed': completed_count,
            'expired': expired_count,
        },
        'current_status': status_filter,
        'current_office': office_filter,
        'search_q': search_q,
        'director_name': request.user.get_full_name() or 'Director',
    }
    return render(request, 'director/active_sa_list.html', context)


@login_required
def director_sa_detail(request, pk):
    """Director's detailed view of an active SA with evaluation capabilities."""
    if not request.user.is_superuser:
        return redirect('home:home')

    sa = get_object_or_404(
        ActiveStudentAssistant.objects.select_related('assigned_office'),
        pk=pk,
    )
    attendance = sa.attendance_records.all()
    evaluations = sa.evaluations.all()

    from decimal import Decimal
    total_days = attendance.count()
    present_days = attendance.filter(status='present').count()
    late_days = attendance.filter(status='late').count()
    absent_days = attendance.filter(status='absent').count()
    excused_days = attendance.filter(status='excused').count()

    # Recalculate total hours
    total_hours = Decimal('0')
    for rec in attendance:
        total_hours += Decimal(str(rec.hours_worked))
    if sa.total_hours != total_hours:
        sa.total_hours = total_hours
        sa.save(update_fields=['total_hours'])

    attendance_form = AttendanceForm(initial={'date': _date.today()})
    evaluation_form = PerformanceEvaluationForm()
    status_form = ActiveSAStatusForm(instance=sa)

    # ── Weekly summary ──
    weekly_summary = _build_weekly_summary(attendance, sa.start_date)

    # ── Semester report ──
    semester_report = _build_semester_report(sa)

    # ── Alerts ──
    consec_count, consec_dates = _check_consecutive_absences(sa)
    consecutive_absence_alert = None
    if consec_count >= CONSECUTIVE_ABSENCE_THRESHOLD:
        consecutive_absence_alert = {'count': consec_count, 'dates': consec_dates, 'threshold': CONSECUTIVE_ABSENCE_THRESHOLD}
    late_count, late_month = _check_late_threshold(sa)
    late_threshold_alert = None
    if late_count >= LATE_MONTHLY_THRESHOLD:
        late_threshold_alert = {'count': late_count, 'month': late_month, 'threshold': LATE_MONTHLY_THRESHOLD}

    context = {
        'sa': sa,
        'attendance': attendance[:30],
        'evaluations': evaluations,
        'attendance_summary': {
            'total_days': total_days,
            'present': present_days,
            'late': late_days,
            'absent': absent_days,
            'excused': excused_days,
        },
        'attendance_form': attendance_form,
        'evaluation_form': evaluation_form,
        'status_form': status_form,
        'director_name': request.user.get_full_name() or 'Director',
        'weekly_summary': weekly_summary,
        'semester_report': semester_report,
        'consecutive_absence_alert': consecutive_absence_alert,
        'late_threshold_alert': late_threshold_alert,
    }
    return render(request, 'director/sa_detail.html', context)


@login_required
@require_POST
def director_log_attendance(request, pk):
    """Director can also log attendance."""
    if not request.user.is_superuser:
        return redirect('home:home')

    sa = get_object_or_404(ActiveStudentAssistant, pk=pk)
    form = AttendanceForm(request.POST)
    if form.is_valid():
        record = form.save(commit=False)
        att_date = record.date

        # ── Attendance blocking checks ──
        if att_date.weekday() >= 5:
            messages.error(request, f'Cannot log attendance on a weekend ({att_date.strftime("%A")}).')
            return redirect('home:director_sa_detail', pk=pk)

        if sa.end_date and att_date > sa.end_date:
            messages.error(request, f'Cannot log attendance past the duty end date ({sa.end_date}).')
            return redirect('home:director_sa_detail', pk=pk)

        from django.db.models import Q as _Q
        is_no_duty = NoDutyDay.objects.filter(
            _Q(office=sa.assigned_office) | _Q(office__isnull=True),
            date=att_date,
        ).exists()
        if is_no_duty:
            messages.error(request, f'{att_date} is a No-Duty Day. Attendance cannot be logged.')
            return redirect('home:director_sa_detail', pk=pk)

        record.student_assistant = sa
        record.logged_by = request.user
        record.save()

        from decimal import Decimal
        sa.total_hours += Decimal(str(record.hours_worked))
        sa.save(update_fields=['total_hours'])

        messages.success(request, f'Attendance logged for {sa.full_name}.')
    else:
        error_list = '; '.join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )
        messages.error(request, f'Failed to log attendance: {error_list}')

    return redirect('home:director_sa_detail', pk=pk)


@login_required
@require_POST
def director_evaluate_sa(request, pk):
    """Submit a performance evaluation for an active SA."""
    if not request.user.is_superuser:
        return redirect('home:home')

    sa = get_object_or_404(ActiveStudentAssistant, pk=pk)
    form = PerformanceEvaluationForm(request.POST)
    if form.is_valid():
        evaluation = form.save(commit=False)
        evaluation.student_assistant = sa
        evaluation.evaluated_by = request.user
        evaluation.save()
        messages.success(request, f'Evaluation submitted for {sa.full_name}.')
    else:
        error_list = '; '.join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )
        messages.error(request, f'Failed to submit evaluation: {error_list}')

    return redirect('home:director_sa_detail', pk=pk)


@login_required
@require_POST
def director_update_sa_status(request, pk):
    """Director updates the status/settings of an active SA."""
    if not request.user.is_superuser:
        return redirect('home:home')

    sa = get_object_or_404(ActiveStudentAssistant, pk=pk)
    form = ActiveSAStatusForm(request.POST, instance=sa)
    if form.is_valid():
        form.save()
        messages.success(request, f'Status updated for {sa.full_name}.')
    else:
        messages.error(request, 'Failed to update SA status.')
    return redirect('home:director_sa_detail', pk=pk)


# ── New views for Features: Schedule Resubmit, Document Resubmit, Notes, Verify ──

def _get_application_by_type(app_type, pk):
    """Return application object or raise 404."""
    if app_type == 'new':
        return get_object_or_404(NewApplication, pk=pk)
    elif app_type == 'renewal':
        return get_object_or_404(RenewalApplication, pk=pk)
    raise Http404('Invalid application type')


def resubmit_schedule(request, app_type, pk):
    """Student resubmits their availability schedule after a mismatch flag."""
    app = _get_application_by_type(app_type, pk)
    if app.status != 'schedule_mismatch':
        messages.error(request, 'This application does not require schedule resubmission.')
        return redirect('home:home')

    if request.method == 'POST':
        form = ScheduleResubmitForm(request.POST)
        if form.is_valid():
            app.availability_schedule = form.cleaned_data['availability_schedule']
            app.schedule_verified = False
            app.status = 'under_review'
            app.schedule_mismatch_note = ''
            app.save()

            ApplicationNote.objects.create(
                **{('new_application' if app_type == 'new' else 'renewal_application'): app},
                note_type='schedule_resubmit',
                content='Student resubmitted availability schedule.',
            )
            send_status_update_email(app, 'schedule_mismatch', 'under_review',
                                     'Your updated availability schedule has been received and is now under review.')
            messages.success(request, 'Your availability schedule has been updated and resubmitted.')
            if request.user.is_authenticated and hasattr(request.user, 'student_profile'):
                return redirect('home:student_dashboard')
            return redirect('home:home')
        else:
            messages.error(request, 'Invalid schedule data. Please try again.')

    # GET — render the schedule grid form
    student_name = ''
    if app_type == 'new':
        student_name = f"{app.first_name} {app.last_name}"
    else:
        student_name = app.full_name

    # Serialize old schedule as JSON so the template JS can pre-check the boxes
    old_schedule_json = json.dumps(app.availability_schedule or {})

    context = {
        'app': app,
        'app_type': app_type,
        'student_name': student_name,
        'mismatch_note': app.schedule_mismatch_note,
        'old_schedule_json': old_schedule_json,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
    }
    return render(request, 'student/resubmit_schedule.html', context)


def resubmit_documents(request, app_type, pk):
    """Student re-uploads requested documents."""
    app = _get_application_by_type(app_type, pk)
    if app.status != 'documents_requested':
        messages.error(request, 'This application does not require document resubmission.')
        return redirect('home:home')

    # Build list of returned documents for the template
    returned = app.returned_documents or {}

    # Map of all document fields with labels
    if app_type == 'new':
        all_doc_fields = [
            ('application_form', 'Application Form'),
            ('id_picture', '2x2 ID Picture'),
            ('barangay_clearance', 'Barangay Clearance'),
            ('parents_itr', "Parent's ITR / Certificate of Indigency"),
            ('enrolment_form', 'Certificate of Enrolment'),
            ('schedule_classes', 'Schedule of Classes'),
            ('proof_insurance', 'Proof of Insurance'),
            ('grades_last_sem', 'Grades Last Semester'),
            ('official_time', 'Official Time'),
        ]
    else:
        all_doc_fields = [
            ('id_picture', 'Updated 2x2 ID Picture'),
            ('enrolment_form', 'Certificate of Enrolment'),
            ('schedule_classes', 'Schedule of Classes'),
            ('grades_last_sem', 'Grades Last Semester'),
            ('proof_insurance', 'Proof of Insurance'),
            ('recommendation_letter', 'Recommendation Letter'),
            ('evaluation_form', 'Evaluation Form'),
        ]

    # Build document items for template — returned docs first, then others
    returned_docs = []
    other_docs = []
    for field_name, label in all_doc_fields:
        current_file = getattr(app, field_name, None)
        entry = {
            'field': field_name,
            'label': label,
            'reason': returned.get(field_name, ''),
            'has_file': bool(current_file),
            'file_url': current_file.url if current_file else '',
        }
        if field_name in returned:
            returned_docs.append(entry)
        else:
            other_docs.append(entry)

    if request.method == 'POST':
        # Inject camera-captured photos into request.FILES before form binding
        _inject_camera_photos(request, [f for f, _ in all_doc_fields])

        form = DocumentResubmitForm(request.POST, request.FILES)
        if form.is_valid():
            for field_name in form.fields:
                uploaded = form.cleaned_data.get(field_name)
                if uploaded:
                    setattr(app, field_name, uploaded)
            app.status = 'under_review'
            app.requested_documents_note = ''
            app.returned_documents = {}
            app.save()

            ApplicationNote.objects.create(
                **{('new_application' if app_type == 'new' else 'renewal_application'): app},
                note_type='document_resubmit',
                content='Student resubmitted requested documents.',
            )
            send_status_update_email(app, 'documents_requested', 'under_review',
                                     'Your updated documents have been received and are now under review.')
            messages.success(request, 'Your documents have been re-uploaded successfully.')
            if request.user.is_authenticated and hasattr(request.user, 'student_profile'):
                return redirect('home:student_dashboard')
            return redirect('home:home')
        else:
            messages.error(request, 'Please correct the errors below and try again.')

    student_name = ''
    if app_type == 'new':
        student_name = f"{app.first_name} {app.last_name}"
    else:
        student_name = app.full_name

    context = {
        'app': app,
        'app_type': app_type,
        'student_name': student_name,
        'returned_docs': returned_docs,
        'other_docs': other_docs,
        'requested_documents_note': app.requested_documents_note,
    }
    return render(request, 'student/resubmit_documents.html', context)


@login_required
@require_POST
def staff_add_note(request, pk):
    """Staff adds an internal note to a new-application."""
    if not request.user.is_staff:
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    content = request.POST.get('note_content', '').strip().title()
    if content:
        ApplicationNote.objects.create(
            new_application=app,
            author=request.user,
            note_type='staff',
            content=content,
        )
        messages.success(request, 'Note added.')
    return redirect('home:staff_review_application', pk=pk)


@login_required
@require_POST
def staff_verify_schedule(request, pk):
    """Staff verifies or flags mismatch on the applicant's availability schedule."""
    if not request.user.is_staff:
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    action = request.POST.get('action')  # 'verify' or 'mismatch'

    if action == 'verify':
        app.schedule_verified = True
        app.save()
        ApplicationNote.objects.create(
            new_application=app, author=request.user,
            note_type='staff',
            content='Availability schedule verified — matches Schedule of Classes.',
        )
        messages.success(request, 'Schedule verified successfully.')

    elif action == 'mismatch':
        mismatch_note = request.POST.get('mismatch_note', '').strip().title()
        old_status = app.status
        app.status = 'schedule_mismatch'
        app.schedule_verified = False
        app.schedule_mismatch_note = mismatch_note
        app.save()

        ApplicationNote.objects.create(
            new_application=app, author=request.user,
            note_type='schedule_mismatch',
            content=f'Schedule mismatch flagged: {mismatch_note}',
        )
        send_schedule_mismatch_email(app, mismatch_note)
        messages.warning(request, 'Schedule mismatch flagged. Student has been notified.')

    return redirect('home:staff_review_application', pk=pk)


@login_required
@require_POST
def director_add_note(request, pk):
    """Director adds an internal note to a new-application."""
    if not request.user.is_superuser:
        return redirect('home:home')
    app = get_object_or_404(NewApplication, pk=pk)
    content = request.POST.get('note_content', '').strip().title()
    if content:
        ApplicationNote.objects.create(
            new_application=app,
            author=request.user,
            note_type='director',
            content=content,
        )
        messages.success(request, 'Note added.')
    return redirect('home:director_review_application', pk=pk)


# ================================================================
#  STUDENT REGISTRATION & AUTHENTICATION
# ================================================================

def verify_email(request, uidb64, token):
    """Email verification endpoint."""
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_decode

    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=['is_active'])
        if hasattr(user, 'student_profile'):
            user.student_profile.email_verified = True
            user.student_profile.save(update_fields=['email_verified'])
        messages.success(request, 'Your email has been verified! You can now log in.')
        return redirect('home:student_login')
    else:
        messages.error(request, 'Invalid or expired verification link.')
        return redirect('home:student_login')


def student_login(request):
    """Login page for students using student_id only."""
    if request.user.is_authenticated:
        if hasattr(request.user, 'student_profile'):
            return redirect('home:student_dashboard')
        return redirect('home:home')

    error = None
    if request.method == 'POST':
        form = StudentLoginForm(request.POST)
        if form.is_valid():
            sid = form.cleaned_data['student_id']
            try:
                profile = StudentProfile.objects.select_related('user').get(student_id=sid)
                user = profile.user
                if not user.is_active:
                    error = 'Your account is not active. Please contact the office.'
                else:
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    return redirect('home:student_dashboard')
            except StudentProfile.DoesNotExist:
                # Auto-create account if the student has an existing application
                app = NewApplication.objects.filter(student_id=sid).order_by('-submitted_at').first()
                if not app:
                    app = RenewalApplication.objects.filter(student_id=sid).order_by('-submitted_at').first()

                if app:
                    if isinstance(app, NewApplication):
                        first_name = app.first_name
                        last_name = app.last_name
                        email = app.email
                    else:
                        # RenewalApplication – split full_name
                        parts = (app.full_name or '').split(None, 1)
                        first_name = parts[0] if parts else ''
                        last_name = parts[1] if len(parts) > 1 else ''
                        email = app.email

                    user = User.objects.create_user(
                        username=sid,
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                    )
                    user.set_unusable_password()
                    user.save(update_fields=['password'])
                    StudentProfile.objects.create(
                        user=user,
                        student_id=sid,
                        full_name=f"{first_name} {last_name}".strip(),
                        email_verified=True,
                    )
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    return redirect('home:student_dashboard')
                else:
                    error = 'No account found for this Student ID. Please register first.'
    else:
        form = StudentLoginForm()

    return render(request, 'student/login.html', {'form': form, 'error': error})


# ================================================================
#  STUDENT DASHBOARD
# ================================================================

@login_required
def student_dashboard(request):
    """Dashboard for authenticated students."""
    if not hasattr(request.user, 'student_profile'):
        return redirect('home:home')

    profile = request.user.student_profile
    student_id = profile.student_id

    today = _date.today()

    # ── Applications ──
    new_apps = list(NewApplication.objects.filter(student_id=student_id).order_by('-submitted_at'))
    renewal_apps = list(RenewalApplication.objects.filter(student_id=student_id).order_by('-submitted_at'))

    applications = []

    for app in new_apps:
        documents = _build_documents_from_app(app)
        steps = _build_steps_from_status(app.status)
        display_status, status_message = STATUS_DISPLAY_MAP.get(
            app.status, ('Under Review', "Your documents are currently being verified.")
        )
        total_steps = len(steps)
        done_steps = sum(1 for s in steps if s['status'] == 'done')
        progress_pct = int((done_steps / total_steps) * 100) if total_steps else 0
        total_docs = len(documents)
        completed_docs = sum(1 for d in documents if d['status'] in ('uploaded', 'done'))
        pending_docs = sum(1 for d in documents if d['status'] in ('pending', 'missing'))

        applications.append({
            'obj': app, 'app_type': 'New Application', 'app_type_key': 'new',
            'student_name': f"{app.first_name} {app.last_name}",
            'application_id': app.student_id, 'documents': documents, 'steps': steps,
            'application_status': display_status, 'status_message': status_message,
            'raw_status': app.status, 'progress_percent': progress_pct,
            'total_steps': total_steps, 'completed_steps': done_steps,
            'total_docs': total_docs, 'completed_docs': completed_docs, 'pending_docs': pending_docs,
            'submitted_at': app.submitted_at,
            'schedule_mismatch_note': app.schedule_mismatch_note if app.status == 'schedule_mismatch' else '',
            'requested_documents_note': app.requested_documents_note if app.status == 'documents_requested' else '',
            'returned_documents': app.returned_documents if app.status == 'documents_requested' else {},
            'availability_schedule': app.availability_schedule or {},
        })

    for app in renewal_apps:
        documents = _build_documents_from_renewal(app)
        steps = _build_steps_from_status(app.status)
        display_status, status_message = STATUS_DISPLAY_MAP.get(
            app.status, ('Under Review', "Your documents are currently being verified.")
        )
        total_steps = len(steps)
        done_steps = sum(1 for s in steps if s['status'] == 'done')
        progress_pct = int((done_steps / total_steps) * 100) if total_steps else 0
        total_docs = len(documents)
        completed_docs = sum(1 for d in documents if d['status'] in ('uploaded', 'done'))
        pending_docs = sum(1 for d in documents if d['status'] in ('pending', 'missing'))

        applications.append({
            'obj': app, 'app_type': 'Renewal Application', 'app_type_key': 'renewal',
            'student_name': app.full_name,
            'application_id': app.student_id, 'documents': documents, 'steps': steps,
            'application_status': display_status, 'status_message': status_message,
            'raw_status': app.status, 'progress_percent': progress_pct,
            'total_steps': total_steps, 'completed_steps': done_steps,
            'total_docs': total_docs, 'completed_docs': completed_docs, 'pending_docs': pending_docs,
            'submitted_at': app.submitted_at,
            'schedule_mismatch_note': app.schedule_mismatch_note if app.status == 'schedule_mismatch' else '',
            'requested_documents_note': app.requested_documents_note if app.status == 'documents_requested' else '',
            'returned_documents': app.returned_documents if app.status == 'documents_requested' else {},
            'availability_schedule': app.availability_schedule or {},
        })

    applications.sort(key=lambda x: x['submitted_at'], reverse=True)

    # ── Active SA records ──
    from django.db.models import Q
    active_sa_records = ActiveStudentAssistant.objects.filter(
        student_id=student_id
    ).select_related('assigned_office')

    sa_data = []
    for sa in active_sa_records:
        attendance = sa.attendance_records.all()[:20]
        evaluations = sa.evaluations.all()

        # Remaining weekdays
        remaining_days = 0
        if sa.end_date and sa.start_date and sa.status == 'active':
            cursor = today
            while cursor <= sa.end_date:
                if cursor.weekday() < 5:
                    remaining_days += 1
                cursor += timedelta(days=1)

        # Upcoming no-duty days within duty period
        ndd_qs = NoDutyDay.objects.filter(
            Q(office=sa.assigned_office) | Q(office__isnull=True),
            date__gte=today,
        )
        if sa.end_date:
            ndd_qs = ndd_qs.filter(date__lte=sa.end_date)
        no_duty_days = list(ndd_qs.order_by('date')[:20])

        # ── Today's shifts and attendance records ──
        ph_now = timezone.localtime()
        day_name = ph_now.strftime('%A')
        now_time = ph_now.time()
        raw_slots = (sa.duty_schedule or {}).get(day_name, [])
        today_shifts_labels = _merge_consecutive_slots(raw_slots)
        today_records = {r.shift: r for r in sa.attendance_records.filter(date=today)}

        # Build per-shift status for the template
        shifts_status = []
        for slot in today_shifts_labels:
            slot_start, slot_end = _parse_slot_times(slot)
            rec = today_records.get(slot)
            earliest_in = (_datetime.combine(today, slot_start) - timedelta(minutes=2)).time() if slot_start else None
            auto_out = (_datetime.combine(today, slot_end) + timedelta(minutes=2)).time() if slot_end else None

            # Auto clock-out: if clocked in but not out, and we're past slot_end + 2 min
            if rec and rec.time_in and not rec.time_out and auto_out and now_time > auto_out:
                rec.time_out = slot_end
                rec.save(update_fields=['time_out'])
                # recalculate total hours
                total = Decimal('0')
                for r in sa.attendance_records.all():
                    total += Decimal(str(r.hours_worked))
                sa.total_hours = total
                sa.save(update_fields=['total_hours'])

            can_clock_in = (
                slot_start and earliest_in and not rec
                and earliest_in <= now_time <= slot_end
            )
            is_on_duty = rec and rec.time_in and not rec.time_out
            is_done = rec and rec.time_in and rec.time_out
            is_past = bool(slot_end and now_time > slot_end and not rec)

            # Real-time absent record: shift ended with no clock-in → create absent record now
            if is_past:
                rec, created = AttendanceRecord.objects.get_or_create(
                    student_assistant=sa,
                    date=today,
                    shift=slot,
                    defaults={'status': 'absent'},
                )
                # Send absent notification email (once per shift)
                if created:
                    from .models import DutyReminder
                    _, notif_new = DutyReminder.objects.get_or_create(
                        student_assistant=sa, date=today,
                        shift=slot, reminder_type='absent',
                    )
                    if notif_new:
                        from .email_utils import send_absent_notification_email
                        send_absent_notification_email(sa, today, slot)

            shifts_status.append({
                'label': slot,
                'start': slot_start,
                'end': slot_end,
                'record': rec,
                'can_clock_in': can_clock_in,
                'is_on_duty': is_on_duty,
                'is_done': is_done,
                'not_yet': slot_start and now_time < earliest_in if earliest_in else False,
                'earliest_in': earliest_in,
                'past': is_past,
            })

        # Re-fetch attendance so newly created absent records appear in Recent Attendance
        attendance = sa.attendance_records.all()[:20]

        # ── Monthly payout summary (₱35/hr, 4 months from start_date) ──
        HOURLY_RATE = Decimal('35.00')
        monthly_payout = []
        if sa.start_date:
            all_records = sa.attendance_records.all()
            hours_by_month = defaultdict(Decimal)
            status_by_month = defaultdict(lambda: {'present': 0, 'late': 0, 'absent': 0})
            for rec in all_records:
                key = (rec.date.year, rec.date.month)
                if rec.time_in and rec.time_out:
                    hours_by_month[key] += Decimal(str(rec.hours_worked))
                if rec.status in ('present', 'late', 'absent'):
                    status_by_month[key][rec.status] += 1

            # Build 4 months starting from the SA's start_date month
            for i in range(4):
                m = sa.start_date.month + i
                y = sa.start_date.year
                if m > 12:
                    m -= 12
                    y += 1
                month_label = _date(y, m, 1).strftime('%B %Y')
                days_in_month = calendar.monthrange(y, m)[1]
                weekdays_in_month = sum(
                    1 for d in range(1, days_in_month + 1)
                    if _date(y, m, d).weekday() < 5
                )
                hours = hours_by_month.get((y, m), Decimal('0'))
                payout = round(hours * HOURLY_RATE, 2)
                counts = status_by_month.get((y, m), {'present': 0, 'late': 0, 'absent': 0})
                monthly_payout.append({
                    'month': month_label,
                    'days': days_in_month,
                    'weekdays': weekdays_in_month,
                    'hours': float(hours),
                    'rate': float(HOURLY_RATE),
                    'payout': float(payout),
                    'present': counts['present'],
                    'late': counts['late'],
                    'absent': counts['absent'],
                })

        # ── Missed shifts (past slots with no record) ──
        missed_shifts = [s['label'] for s in shifts_status if s.get('past')]

        # ── Weekly summary ──
        all_records_for_weekly = sa.attendance_records.all()
        weekly_summary = _build_weekly_summary(all_records_for_weekly, sa.start_date)

        # ── Semester report ──
        semester_report = _build_semester_report(sa)

        # ── Consecutive absence alert ──
        consec_count, consec_dates = _check_consecutive_absences(sa)
        consecutive_absence_alert = None
        if consec_count >= CONSECUTIVE_ABSENCE_THRESHOLD:
            consecutive_absence_alert = {
                'count': consec_count,
                'dates': consec_dates,
                'threshold': CONSECUTIVE_ABSENCE_THRESHOLD,
            }

        # ── Late threshold alert ──
        late_count, late_month = _check_late_threshold(sa)
        late_threshold_alert = None
        if late_count >= LATE_MONTHLY_THRESHOLD:
            late_threshold_alert = {
                'count': late_count,
                'month': late_month,
                'threshold': LATE_MONTHLY_THRESHOLD,
            }

        sa_data.append({
            'sa': sa,
            'attendance': attendance,
            'evaluations': evaluations,
            'remaining_days': remaining_days,
            'no_duty_days': no_duty_days,
            'shifts_status': shifts_status,
            'has_schedule': bool(sa.duty_schedule),
            'today_day': day_name,
            'monthly_payout': monthly_payout,
            'missed_shifts': missed_shifts,
            'weekly_summary': weekly_summary,
            'semester_report': semester_report,
            'consecutive_absence_alert': consecutive_absence_alert,
            'late_threshold_alert': late_threshold_alert,
        })

    # ── Approved Student Assistants (public list) ──
    approved_new = NewApplication.objects.filter(status='approved').order_by('-submitted_at')
    approved_renewal = RenewalApplication.objects.filter(status='approved').order_by('-submitted_at')
    approved_students = []
    for app in approved_new:
        approved_students.append({
            'name': f"{app.first_name} {app.last_name}",
            'student_id': app.student_id,
            'course': app.course,
            'office': app.assigned_office or '—',
            'start_date': app.start_date,
            'submitted_at': app.submitted_at,
        })
    for app in approved_renewal:
        approved_students.append({
            'name': app.full_name,
            'student_id': app.student_id,
            'course': app.course,
            'office': app.assigned_office or '—',
            'start_date': app.start_date,
            'submitted_at': app.submitted_at,
        })
    approved_students.sort(key=lambda x: x['submitted_at'], reverse=True)

    # ── Reminders & Announcements (same logic as home view) ──
    from django.db.models import Q
    reminder_filter = Q(student__isnull=True, is_active=True) & (
        Q(expires_at__isnull=True) | Q(expires_at__gte=today)
    )
    db_reminders = Reminder.objects.filter(reminder_filter).order_by('-created_at')
    reminders = [
        {
            'message': r.message,
            'priority': r.priority,
            'id': r.id,
            'created_at': r.created_at.strftime('%b %d, %Y'),
        }
        for r in db_reminders
    ]

    db_announcements = Announcement.objects.filter(is_active=True).exclude(
        expires_at__isnull=False, expires_at__lt=today
    )[:6]
    seven_days_ago = timezone.now() - timedelta(days=7)
    announcements = [
        {
            'title': a.title,
            'summary': a.summary,
            'image': a.image,
            'published_at': a.published_at.strftime('%b %d, %Y'),
            'is_new': a.published_at >= seven_days_ago,
        }
        for a in db_announcements
    ]

    context = {
        'profile': profile,
        'applications': applications,
        'has_application': len(applications) > 0,
        'sa_data': sa_data,
        'approved_students': approved_students,
        'today': today,
        'day_choices': DAY_CHOICES,
        'time_slot_choices': TIME_SLOT_CHOICES,
        'reminders': reminders,
        'announcements': announcements,
    }
    return render(request, 'student/dashboard.html', context)



CONSECUTIVE_ABSENCE_THRESHOLD = 3   # consecutive duty-days absent
LATE_MONTHLY_THRESHOLD = 5          # late records per month


def _build_weekly_summary(attendance_qs, start_date):
    """Group attendance records into ISO-week buckets."""
    if not start_date:
        return []
    weekly = defaultdict(lambda: {'present': 0, 'late': 0, 'absent': 0, 'hours': Decimal('0')})
    for rec in attendance_qs:
        iso_year, iso_week, _ = rec.date.isocalendar()
        key = (iso_year, iso_week)
        if rec.status in ('present', 'late', 'absent'):
            weekly[key][rec.status] += 1
        if rec.time_in and rec.time_out:
            weekly[key]['hours'] += Decimal(str(rec.hours_worked))
    if not weekly:
        return []
    result = []
    for (yr, wk) in sorted(weekly.keys(), reverse=True):
        # Monday of that ISO week
        from datetime import datetime as _dt
        monday = _dt.strptime(f'{yr} {wk} 1', '%G %V %u').date()
        sunday = monday + timedelta(days=6)
        label = f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"
        data = weekly[(yr, wk)]
        result.append({
            'label': label,
            'present': data['present'],
            'late': data['late'],
            'absent': data['absent'],
            'hours': float(data['hours']),
        })
    return result[:8]  # last 8 weeks


def _build_semester_report(sa):
    """Build a semester-wide attendance summary."""
    all_records = sa.attendance_records.all()
    total = all_records.count()
    if total == 0:
        return None
    present = all_records.filter(status='present').count()
    late = all_records.filter(status='late').count()
    absent = all_records.filter(status='absent').count()
    excused = all_records.filter(status='excused').count()
    attended = present + late + excused
    attendance_rate = round(attended / total * 100, 1) if total else 0
    total_hours = float(sa.total_hours)
    required_hours = sa.required_hours
    hours_pct = round(total_hours / required_hours * 100, 1) if required_hours else 0

    # ── System-driven renewal recommendation ──
    final_eval = sa.evaluations.filter(evaluation_period='final').first()
    midterm_eval = sa.evaluations.filter(evaluation_period='midterm').first()
    latest_eval = final_eval or midterm_eval
    system_recommendation = _compute_renewal_recommendation(
        attendance_rate, total_hours, required_hours, latest_eval
    )

    return {
        'semester': sa.get_semester_display(),
        'academic_year': sa.academic_year,
        'total': total,
        'present': present,
        'late': late,
        'absent': absent,
        'excused': excused,
        'attendance_rate': attendance_rate,
        'total_hours': total_hours,
        'required_hours': required_hours,
        'hours_pct': hours_pct,
        'system_recommendation': system_recommendation,
    }


def _check_consecutive_absences(sa):
    """Check for consecutive duty-day absences (most recent streak)."""
    records = (
        sa.attendance_records
        .filter(status='absent')
        .order_by('-date')
        .values_list('date', flat=True)
    )
    if not records:
        return 0, []
    unique_dates = sorted(set(records), reverse=True)
    # Walk backwards from the most recent absent date counting consecutive weekdays
    streak = [unique_dates[0]]
    cursor = unique_dates[0]
    used = set(unique_dates)
    while True:
        # Find previous weekday
        prev = cursor - timedelta(days=1)
        while prev.weekday() >= 5:  # skip weekends
            prev -= timedelta(days=1)
        if prev in used:
            streak.append(prev)
            cursor = prev
        else:
            break
    streak.reverse()
    return len(streak), streak


def _check_late_threshold(sa):
    """Count late records in the current month."""
    today = _date.today()
    count = sa.attendance_records.filter(
        status='late',
        date__year=today.year,
        date__month=today.month,
    ).count()
    month_label = today.strftime('%B %Y')
    return count, month_label


# ================================================================
#  STUDENT CLOCK-IN / CLOCK-OUT  &  DUTY SCHEDULE
# ================================================================

def _parse_slot_times(slot_label):
    """Parse '8:00 AM - 9:00 AM' → (time(8,0), time(9,0))."""
    from datetime import time as _time
    parts = slot_label.split(' - ')
    if len(parts) != 2:
        return None, None
    fmt = '%I:%M %p'
    try:
        start = _datetime.strptime(parts[0].strip(), fmt).time()
        end = _datetime.strptime(parts[1].strip(), fmt).time()
        return start, end
    except ValueError:
        return None, None


def _fmt_time_no_pad(t):
    """Format a time as '7:30 AM' (no leading zero on hour)."""
    return t.strftime('%I:%M %p').lstrip('0')


def _merge_consecutive_slots(slot_labels):
    """Merge consecutive 30-min slots into continuous shifts.

    ['7:30 AM - 8:00 AM', '8:00 AM - 8:30 AM', '8:30 AM - 9:00 AM']
    → ['7:30 AM - 9:00 AM']
    """
    if not slot_labels:
        return []
    parsed = []
    for label in slot_labels:
        start, end = _parse_slot_times(label)
        if start and end:
            parsed.append((start, end))
    parsed.sort(key=lambda x: (x[0].hour, x[0].minute))

    merged = []
    cur_start, cur_end = parsed[0]
    for s, e in parsed[1:]:
        if s == cur_end:
            cur_end = e          # consecutive → extend
        else:
            merged.append(f'{_fmt_time_no_pad(cur_start)} - {_fmt_time_no_pad(cur_end)}')
            cur_start, cur_end = s, e
    merged.append(f'{_fmt_time_no_pad(cur_start)} - {_fmt_time_no_pad(cur_end)}')
    return merged


def _get_today_shifts(sa):
    """Return list of merged shift labels for today based on the SA's duty_schedule."""
    if not sa.duty_schedule:
        return []
    ph_now = timezone.localtime()
    day_name = ph_now.strftime('%A')  # e.g. 'Monday'
    raw_slots = sa.duty_schedule.get(day_name, [])
    return _merge_consecutive_slots(raw_slots)


# ── Attendance summary helpers ──

CONSECUTIVE_ABSENCE_THRESHOLD = 3
LATE_MONTHLY_THRESHOLD = 5


def _build_weekly_summary(attendance_qs, start_date):
    """Group attendance by ISO week: {week_label, present, late, absent, hours}."""
    from collections import defaultdict
    weeks = defaultdict(lambda: {'present': 0, 'late': 0, 'absent': 0, 'excused': 0, 'hours': Decimal('0')})
    for rec in attendance_qs:
        iso_year, iso_week, _ = rec.date.isocalendar()
        key = (iso_year, iso_week)
        if rec.status in ('present', 'late', 'absent', 'excused'):
            weeks[key][rec.status] += 1
        if rec.time_in and rec.time_out:
            weeks[key]['hours'] += Decimal(str(rec.hours_worked))

    result = []
    for (iso_year, iso_week) in sorted(weeks.keys()):
        # Monday of that ISO week
        from datetime import date as _d
        try:
            week_start = _d.fromisocalendar(iso_year, iso_week, 1)
            week_end = _d.fromisocalendar(iso_year, iso_week, 5)  # Friday
        except (ValueError, AttributeError):
            week_start = week_end = None
        w = weeks[(iso_year, iso_week)]
        result.append({
            'week_num': iso_week,
            'start': week_start,
            'end': week_end,
            'label': f"Week {iso_week} ({week_start.strftime('%b %d') if week_start else '?'} – {week_end.strftime('%b %d') if week_end else '?'})",
            'present': w['present'],
            'late': w['late'],
            'absent': w['absent'],
            'excused': w['excused'],
            'hours': float(w['hours']),
        })
    return result


def _build_semester_report(sa):
    """Build a semester-wide attendance summary for an SA."""
    all_records = sa.attendance_records.all()
    total = all_records.count()
    present = all_records.filter(status='present').count()
    late = all_records.filter(status='late').count()
    absent = all_records.filter(status='absent').count()
    excused = all_records.filter(status='excused').count()
    total_hours = sum(Decimal(str(r.hours_worked)) for r in all_records)
    attendance_rate = round((present + late) / total * 100, 1) if total else 0
    return {
        'total': total,
        'present': present,
        'late': late,
        'absent': absent,
        'excused': excused,
        'total_hours': float(total_hours),
        'required_hours': sa.required_hours,
        'hours_pct': sa.hours_percentage,
        'attendance_rate': attendance_rate,
        'semester': sa.get_semester_display(),
        'academic_year': sa.academic_year,
    }


def _check_consecutive_absences(sa):
    """Check if student has >= THRESHOLD consecutive absent *dates* (most recent)."""
    records = (sa.attendance_records
               .filter(status='absent')
               .order_by('-date')
               .values_list('date', flat=True))
    if not records:
        return 0, []
    unique_dates = sorted(set(records), reverse=True)
    streak = 1
    streak_dates = [unique_dates[0]]
    for i in range(1, len(unique_dates)):
        # Count weekday gaps (skip weekends)
        prev, cur = unique_dates[i - 1], unique_dates[i]
        diff_days = (prev - cur).days
        # If dates are consecutive weekdays (1 day or 3 days for Fri→Mon)
        if diff_days <= 3 and cur.weekday() < 5:
            streak += 1
            streak_dates.append(cur)
        else:
            break
    return streak, list(reversed(streak_dates))


def _check_late_threshold(sa):
    """Check if student exceeded LATE_MONTHLY_THRESHOLD this month."""
    today = _date.today()
    late_count = sa.attendance_records.filter(
        status='late',
        date__year=today.year,
        date__month=today.month,
    ).count()
    month_label = today.strftime('%B %Y')
    return late_count, month_label


@login_required
@require_POST
def student_save_duty_schedule(request, pk):
    """Student sets or updates their duty schedule."""
    if not hasattr(request.user, 'student_profile'):
        return redirect('home:home')
    sa = get_object_or_404(ActiveStudentAssistant, pk=pk, student_id=request.user.student_profile.student_id)

    raw = request.POST.get('duty_schedule', '{}')
    try:
        schedule = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        schedule = {}

    if not schedule:
        messages.error(request, 'Please select at least one time slot.')
        return redirect('home:student_dashboard')

    # Validate min 1 hour / max 4 hours per day (each slot = 0.5 hrs)
    for day, slots in schedule.items():
        day_hours = len(slots) * 0.5
        if day_hours < 1:
            messages.error(request, f'Minimum 1 hour per day — {day} has only {day_hours:.1f} hours.')
            return redirect('home:student_dashboard')
        if day_hours > 4:
            messages.error(request, f'Maximum 4 hours per day — {day} has {day_hours:.1f} hours.')
            return redirect('home:student_dashboard')

    sa.duty_schedule = schedule
    sa.save(update_fields=['duty_schedule'])
    messages.success(request, 'Duty schedule saved successfully!')
    return redirect('home:student_dashboard')


@login_required
@require_POST
def student_clock_in(request, pk):
    """Student clocks in for a specific shift."""
    if not hasattr(request.user, 'student_profile'):
        return redirect('home:home')
    sa = get_object_or_404(ActiveStudentAssistant, pk=pk, student_id=request.user.student_profile.student_id, status='active')

    if not sa.duty_schedule:
        messages.error(request, 'Please set your duty schedule first.')
        return redirect('home:student_dashboard')

    ph_now = timezone.localtime()
    today = ph_now.date()
    now = ph_now.time()
    shift_label = request.POST.get('shift', '')

    if not shift_label:
        messages.error(request, 'No shift specified.')
        return redirect('home:student_dashboard')

    # Verify this shift is in today's schedule
    today_shifts = _get_today_shifts(sa)
    if shift_label not in today_shifts:
        messages.error(request, 'This shift is not in your schedule for today.')
        return redirect('home:student_dashboard')

    # Check 2-min-before window
    slot_start, slot_end = _parse_slot_times(shift_label)
    if not slot_start:
        messages.error(request, 'Invalid shift format.')
        return redirect('home:student_dashboard')

    # Allow clock-in from 2 minutes before shift start until shift end
    earliest = (_datetime.combine(today, slot_start) - timedelta(minutes=2)).time()
    if now < earliest:
        messages.info(request, f'Clock-in opens at {earliest.strftime("%I:%M %p")} (2 min before shift).')
        return redirect('home:student_dashboard')
    if now > slot_end:
        messages.info(request, 'This shift has already ended.')
        return redirect('home:student_dashboard')

    # Check daily 4-hour cap
    today_records = AttendanceRecord.objects.filter(student_assistant=sa, date=today)
    today_total = sum(Decimal(str(r.hours_worked)) for r in today_records)
    if today_total >= 4:
        messages.info(request, 'You have already reached the 4-hour daily limit.')
        return redirect('home:student_dashboard')

    clock_status = 'late' if now > slot_start else 'present'
    record, created = AttendanceRecord.objects.get_or_create(
        student_assistant=sa,
        date=today,
        shift=shift_label,
        defaults={'time_in': now, 'status': clock_status, 'logged_by': request.user},
    )
    if created:
        messages.success(request, f'Clocked in at {now.strftime("%I:%M %p")} for {shift_label}.')
    else:
        messages.info(request, f'Already clocked in for {shift_label}.')
    return redirect('home:student_dashboard')


@login_required
@require_POST
def student_clock_out(request, pk):
    """Student clocks out for a specific shift and updates total hours."""
    if not hasattr(request.user, 'student_profile'):
        return redirect('home:home')
    sa = get_object_or_404(ActiveStudentAssistant, pk=pk, student_id=request.user.student_profile.student_id, status='active')
    ph_now = timezone.localtime()
    today = ph_now.date()
    now = ph_now.time()
    shift_label = request.POST.get('shift', '')

    try:
        record = AttendanceRecord.objects.get(student_assistant=sa, date=today, shift=shift_label)
    except AttendanceRecord.DoesNotExist:
        messages.error(request, 'You need to clock in first.')
        return redirect('home:student_dashboard')

    if record.time_out:
        messages.info(request, f'Already clocked out for {shift_label}.')
        return redirect('home:student_dashboard')

    record.time_out = now
    record.save(update_fields=['time_out'])

    # Update the SA's cached total_hours
    total = Decimal('0')
    for rec in sa.attendance_records.all():
        total += Decimal(str(rec.hours_worked))
    sa.total_hours = total
    sa.save(update_fields=['total_hours'])

    messages.success(request, f'Clocked out at {now.strftime("%I:%M %p")}. Hours: {record.hours_worked}')
    return redirect('home:student_dashboard')


# ================================================================
#  NO-DUTY DAY MANAGEMENT (Staff)
# ================================================================

@login_required
@require_POST
def staff_add_no_duty_day(request):
    """Staff adds a no-duty day."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    form = NoDutyDayForm(request.POST)
    if form.is_valid():
        ndd = form.save(commit=False)
        ndd.created_by = request.user
        ndd.save()
        # Recalculate end dates for affected SAs
        recalculate_end_dates_for_office(ndd.office)
        messages.success(request, f'No-Duty Day added: {ndd.date} — {ndd.reason}')
    else:
        error_list = '; '.join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )
        messages.error(request, f'Failed to add No-Duty Day: {error_list}')
    return redirect('home:staff_dashboard')


@login_required
@require_POST
def staff_delete_no_duty_day(request, pk):
    """Staff deletes a no-duty day."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    ndd = get_object_or_404(NoDutyDay, pk=pk)
    office = ndd.office
    ndd.delete()
    # Recalculate end dates after removal
    recalculate_end_dates_for_office(office)
    messages.success(request, 'No-Duty Day removed.')
    return redirect('home:staff_dashboard')


# ================================================================
#  CSV EXPORT VIEWS
# ================================================================

def _make_csv_response(filename, header, rows):
    """Helper to build an HttpResponse with CSV content."""
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


@login_required
def staff_export_applications_csv(request):
    """Export all applications as CSV (staff)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    header = [
        'Type', 'Student ID', 'First Name', 'Last Name', 'Email', 'Contact',
        'Course', 'Year Level', 'Semester', 'GPA', 'Preferred Office', 'Status',
        'Submitted At',
    ]
    rows = []
    for app in NewApplication.objects.select_related('preferred_office').order_by('-submitted_at'):
        rows.append([
            'New', app.student_id, app.first_name, app.last_name,
            app.email, app.contact_number, app.course, app.year_level,
            app.semester, app.gpa or '', app.preferred_office or '',
            app.get_status_display(), app.submitted_at.strftime('%Y-%m-%d %H:%M'),
        ])
    for app in RenewalApplication.objects.select_related('preferred_office').order_by('-submitted_at'):
        rows.append([
            'Renewal', app.student_id, app.first_name, app.last_name,
            app.email, app.contact_number, app.course, app.year_level,
            app.semester, app.gpa or '', app.preferred_office or '',
            app.get_status_display(), app.submitted_at.strftime('%Y-%m-%d %H:%M'),
        ])
    return _make_csv_response('applications_export.csv', header, rows)


@login_required
def staff_export_active_sa_csv(request):
    """Export active student assistants as CSV (staff)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    header = [
        'Student ID', 'Full Name', 'Email', 'Course', 'Assigned Office',
        'Semester', 'Academic Year', 'Start Date', 'End Date',
        'Total Hours', 'Required Hours', 'Status',
    ]
    rows = []
    for sa in ActiveStudentAssistant.objects.select_related('assigned_office').order_by('-created_at'):
        rows.append([
            sa.student_id, sa.full_name, sa.email, sa.course,
            sa.assigned_office or '', sa.semester, sa.academic_year,
            sa.start_date or '', sa.end_date or '',
            sa.total_hours, sa.required_hours, sa.get_status_display(),
        ])
    return _make_csv_response('active_sa_export.csv', header, rows)


@login_required
def staff_export_attendance_csv(request):
    """Export attendance records as CSV (staff)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')
    header = [
        'Student ID', 'Full Name', 'Office', 'Date', 'Shift',
        'Time In', 'Time Out', 'Hours Worked', 'Status', 'Remarks',
    ]
    rows = []
    for att in AttendanceRecord.objects.select_related('student_assistant', 'student_assistant__assigned_office').order_by('-date', '-time_in'):
        sa = att.student_assistant
        rows.append([
            sa.student_id, sa.full_name, sa.assigned_office or '',
            att.date, att.shift, att.time_in or '', att.time_out or '',
            att.hours_worked, att.get_status_display(), att.remarks,
        ])
    return _make_csv_response('attendance_export.csv', header, rows)


@login_required
def director_export_evaluations_csv(request):
    if not request.user.is_superuser:
        return redirect('home:home')
    header = [
        'Student ID', 'Full Name', 'Office', 'Period',
        'Work Quality', 'Punctuality', 'Initiative', 'Cooperation',
        'Communication', 'Overall Rating', 'Recommendation', 'Remarks', 'Evaluated By', 'Date',
    ]
    rows = []
    for ev in PerformanceEvaluation.objects.select_related('student_assistant', 'student_assistant__assigned_office', 'evaluated_by').order_by('-evaluated_at'):
        sa = ev.student_assistant
        rows.append([
            sa.student_id, sa.full_name, sa.assigned_office or '',
            ev.get_evaluation_period_display(),
            ev.work_quality, ev.punctuality, ev.initiative,
            ev.cooperation, ev.communication, ev.overall_rating,
            ev.get_recommendation_status_display() if ev.recommendation_status else '',
            ev.remarks, ev.evaluated_by or '', ev.evaluated_at.strftime('%Y-%m-%d %H:%M'),
        ])
    return _make_csv_response('evaluations_export.csv', header, rows)


# ================================================================
#  DEPARTMENT-LEVEL REPORTS  (Module 7.1)
# ================================================================

@login_required
def director_department_reports(request):
    """Per-office aggregation: attendance averages, productivity averages, eval summary."""
    if not request.user.is_superuser:
        return redirect('home:home')

    from django.db.models import Avg, Count, FloatField, Q, Sum, F
    from django.db.models.functions import Coalesce

    offices = Office.objects.filter(is_active=True).order_by('name')
    report = []

    for office in offices:
        sas = ActiveStudentAssistant.objects.filter(assigned_office=office)
        sa_count = sas.count()
        active_count = sas.filter(status='active').count()

        att_qs = AttendanceRecord.objects.filter(student_assistant__assigned_office=office)
        att_total = att_qs.count()
        att_present = att_qs.filter(status='present').count()
        att_late = att_qs.filter(status='late').count()
        att_absent = att_qs.filter(status='absent').count()
        att_excused = att_qs.filter(status='excused').count()
        attended = att_present + att_late + att_excused
        attendance_rate = round(attended / att_total * 100, 1) if att_total else 0


        hours_agg = sas.aggregate(
            avg_hours=Coalesce(Avg('total_hours'), 0.0, output_field=FloatField()),
            total_hours=Coalesce(Sum('total_hours'), 0.0, output_field=FloatField()),
            avg_required=Coalesce(Avg('required_hours'), 0.0, output_field=FloatField()),
        )
        avg_hours = round(float(hours_agg['avg_hours']), 1)
        total_hours = round(float(hours_agg['total_hours']), 1)
        avg_required = round(float(hours_agg['avg_required']), 1)
        avg_completion = round(avg_hours / avg_required * 100, 1) if avg_required else 0


        eval_qs = PerformanceEvaluation.objects.filter(student_assistant__assigned_office=office)
        eval_agg = eval_qs.aggregate(
            count=Count('id'),
            avg_quality=Avg('work_quality'),
            avg_punctuality=Avg('punctuality'),
            avg_initiative=Avg('initiative'),
            avg_cooperation=Avg('cooperation'),
            avg_communication=Avg('communication'),
            avg_overall=Avg('overall_rating'),
        )
        eval_count = eval_agg['count'] or 0

        def _r(v):
            return round(float(v), 2) if v else 0

        report.append({
            'office': office,
            'sa_count': sa_count,
            'active_count': active_count,

            'att_total': att_total,
            'att_present': att_present,
            'att_late': att_late,
            'att_absent': att_absent,
            'att_excused': att_excused,
            'attendance_rate': attendance_rate,
            # productivity
            'avg_hours': avg_hours,
            'total_hours': total_hours,
            'avg_required': avg_required,
            'avg_completion': avg_completion,
            # evaluations
            'eval_count': eval_count,
            'avg_quality': _r(eval_agg['avg_quality']),
            'avg_punctuality': _r(eval_agg['avg_punctuality']),
            'avg_initiative': _r(eval_agg['avg_initiative']),
            'avg_cooperation': _r(eval_agg['avg_cooperation']),
            'avg_communication': _r(eval_agg['avg_communication']),
            'avg_overall': _r(eval_agg['avg_overall']),
        })


    all_att = AttendanceRecord.objects.all()
    g_att_total = all_att.count()
    g_attended = all_att.filter(status__in=['present', 'late', 'excused']).count()
    g_attendance_rate = round(g_attended / g_att_total * 100, 1) if g_att_total else 0

    all_sas = ActiveStudentAssistant.objects.all()
    g_hours = all_sas.aggregate(
        avg_hours=Coalesce(Avg('total_hours'), 0.0, output_field=FloatField()),
        total_hours=Coalesce(Sum('total_hours'), 0.0, output_field=FloatField()),
    )

    all_evals = PerformanceEvaluation.objects.all()
    g_eval = all_evals.aggregate(avg_overall=Avg('overall_rating'), count=Count('id'))

    context = {
        'report': report,
        'offices': offices,
        'global_stats': {
            'total_sas': all_sas.count(),
            'active_sas': all_sas.filter(status='active').count(),
            'attendance_rate': g_attendance_rate,
            'avg_hours': round(float(g_hours['avg_hours']), 1),
            'total_hours': round(float(g_hours['total_hours']), 1),
            'avg_overall': round(float(g_eval['avg_overall']), 2) if g_eval['avg_overall'] else 0,
            'eval_count': g_eval['count'] or 0,
        },
        'director_name': request.user.get_full_name() or 'Director',
    }
    return render(request, 'director/department_reports.html', context)


def _build_department_report_data():
    from django.db.models import Avg, Count, FloatField, Sum
    from django.db.models.functions import Coalesce

    offices = Office.objects.filter(is_active=True).order_by('name')
    report = []

    for office in offices:
        sas = ActiveStudentAssistant.objects.filter(assigned_office=office)
        sa_count = sas.count()
        active_count = sas.filter(status='active').count()

        att_qs = AttendanceRecord.objects.filter(student_assistant__assigned_office=office)
        att_total = att_qs.count()
        att_present = att_qs.filter(status='present').count()
        att_late = att_qs.filter(status='late').count()
        att_absent = att_qs.filter(status='absent').count()
        att_excused = att_qs.filter(status='excused').count()
        attended = att_present + att_late + att_excused
        attendance_rate = round(attended / att_total * 100, 1) if att_total else 0

        hours_agg = sas.aggregate(
            avg_hours=Coalesce(Avg('total_hours'), 0.0, output_field=FloatField()),
            total_hours=Coalesce(Sum('total_hours'), 0.0, output_field=FloatField()),
            avg_required=Coalesce(Avg('required_hours'), 0.0, output_field=FloatField()),
        )
        avg_hours = round(float(hours_agg['avg_hours']), 1)
        total_hours = round(float(hours_agg['total_hours']), 1)
        avg_required = round(float(hours_agg['avg_required']), 1)
        avg_completion = round(avg_hours / avg_required * 100, 1) if avg_required else 0

        eval_qs = PerformanceEvaluation.objects.filter(student_assistant__assigned_office=office)
        eval_agg = eval_qs.aggregate(
            count=Count('id'),
            avg_quality=Avg('work_quality'),
            avg_punctuality=Avg('punctuality'),
            avg_initiative=Avg('initiative'),
            avg_cooperation=Avg('cooperation'),
            avg_communication=Avg('communication'),
            avg_overall=Avg('overall_rating'),
        )

        def _r(v):
            return round(float(v), 2) if v else 0

        report.append({
            'office_name': office.name,
            'building': office.building,
            'head': office.head or '—',
            'sa_count': sa_count,
            'active_count': active_count,
            'att_total': att_total, 'att_present': att_present, 'att_late': att_late,
            'att_absent': att_absent, 'att_excused': att_excused, 'attendance_rate': attendance_rate,
            'avg_hours': avg_hours, 'total_hours': total_hours,
            'avg_required': avg_required, 'avg_completion': avg_completion,
            'eval_count': eval_agg['count'] or 0,
            'avg_quality': _r(eval_agg['avg_quality']),
            'avg_punctuality': _r(eval_agg['avg_punctuality']),
            'avg_initiative': _r(eval_agg['avg_initiative']),
            'avg_cooperation': _r(eval_agg['avg_cooperation']),
            'avg_communication': _r(eval_agg['avg_communication']),
            'avg_overall': _r(eval_agg['avg_overall']),
        })

    all_att = AttendanceRecord.objects.all()
    g_att_total = all_att.count()
    g_attended = all_att.filter(status__in=['present', 'late', 'excused']).count()

    all_sas = ActiveStudentAssistant.objects.all()
    g_hours = all_sas.aggregate(
        avg_hours=Coalesce(Avg('total_hours'), 0.0, output_field=FloatField()),
        total_hours=Coalesce(Sum('total_hours'), 0.0, output_field=FloatField()),
    )
    all_evals = PerformanceEvaluation.objects.all()
    g_eval = all_evals.aggregate(avg_overall=Avg('overall_rating'), count=Count('id'))

    global_stats = {
        'total_sas': all_sas.count(),
        'active_sas': all_sas.filter(status='active').count(),
        'attendance_rate': round(g_attended / g_att_total * 100, 1) if g_att_total else 0,
        'avg_hours': round(float(g_hours['avg_hours']), 1),
        'total_hours': round(float(g_hours['total_hours']), 1),
        'avg_overall': round(float(g_eval['avg_overall']), 2) if g_eval['avg_overall'] else 0,
        'eval_count': g_eval['count'] or 0,
    }
    return report, global_stats


def _render_department_report_pdf(report, global_stats):
    """Generate a department-reports PDF (landscape letter) and return the bytes."""
    from io import BytesIO
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = BytesIO()
    page = landscape(letter)
    doc = SimpleDocTemplate(buf, pagesize=page,
                            leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle('Title', parent=styles['Title'],
                            fontSize=16, textColor=colors.HexColor('#14532d'),
                            spaceAfter=4)
    elements.append(Paragraph('CARLOS HILADO MEMORIAL STATE UNIVERSITY', title_style))

    sub_style = ParagraphStyle('Sub', parent=styles['Normal'],
                                fontSize=10, textColor=colors.HexColor('#166534'),
                                alignment=1, spaceAfter=2)
    elements.append(Paragraph('Talisay City, Negros Occidental', sub_style))

    heading_style = ParagraphStyle('H2', parent=styles['Heading2'],
                                    fontSize=13, textColor=colors.HexColor('#14532d'),
                                    alignment=1, spaceAfter=12)
    elements.append(Paragraph('Department-Level Reports', heading_style))

    date_style = ParagraphStyle('Date', parent=styles['Normal'],
                                fontSize=9, textColor=colors.HexColor('#6b7280'),
                                alignment=1, spaceAfter=8)
    elements.append(Paragraph(f'Generated: {_date.today().strftime("%B %d, %Y")}', date_style))


    gs = global_stats
    summary_data = [[
        'Total SAs', 'Active', 'Attendance Rate', 'Avg Hours/SA', 'Avg Rating', 'Evaluations',
    ], [
        str(gs['total_sas']), str(gs['active_sas']),
        f"{gs['attendance_rate']}%", str(gs['avg_hours']),
        f"{gs['avg_overall']}/5", str(gs['eval_count']),
    ]]
    summary_table = Table(summary_data, hAlign='CENTER')
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#166534')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 14))


    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8, leading=10)
    bold_cell = ParagraphStyle('BoldCell', parent=cell_style, fontName='Helvetica-Bold')

    header = [
        Paragraph('<b>Office</b>', bold_cell),
        Paragraph('<b>SAs<br/>(Active/Total)</b>', bold_cell),
        Paragraph('<b>Attendance<br/>Rate</b>', bold_cell),
        Paragraph('<b>Present</b>', bold_cell),
        Paragraph('<b>Late</b>', bold_cell),
        Paragraph('<b>Absent</b>', bold_cell),
        Paragraph('<b>Avg Hrs</b>', bold_cell),
        Paragraph('<b>Completion</b>', bold_cell),
        Paragraph('<b>Total Hrs</b>', bold_cell),
        Paragraph('<b>Avg Rating</b>', bold_cell),
        Paragraph('<b>Evals</b>', bold_cell),
    ]
    data = [header]

    for r in report:
        data.append([
            Paragraph(r['office_name'], cell_style),
            Paragraph(f"{r['active_count']}/{r['sa_count']}", cell_style),
            Paragraph(f"{r['attendance_rate']}%", cell_style),
            Paragraph(str(r['att_present']), cell_style),
            Paragraph(str(r['att_late']), cell_style),
            Paragraph(str(r['att_absent']), cell_style),
            Paragraph(f"{r['avg_hours']}", cell_style),
            Paragraph(f"{r['avg_completion']}%", cell_style),
            Paragraph(f"{r['total_hours']}", cell_style),
            Paragraph(f"{r['avg_overall']}/5", cell_style),
            Paragraph(str(r['eval_count']), cell_style),
        ])

    col_widths = [140, 55, 60, 48, 40, 44, 52, 62, 56, 60, 42]
    t = Table(data, colWidths=col_widths, hAlign='CENTER', repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#166534')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))


    eval_header = [
        Paragraph('<b>Office</b>', bold_cell),
        Paragraph('<b>Quality</b>', bold_cell),
        Paragraph('<b>Punctuality</b>', bold_cell),
        Paragraph('<b>Initiative</b>', bold_cell),
        Paragraph('<b>Cooperation</b>', bold_cell),
        Paragraph('<b>Communication</b>', bold_cell),
        Paragraph('<b>Overall</b>', bold_cell),
    ]
    eval_data = [eval_header]
    has_evals = False
    for r in report:
        if r['eval_count'] > 0:
            has_evals = True
            eval_data.append([
                Paragraph(r['office_name'], cell_style),
                Paragraph(f"{r['avg_quality']}/5", cell_style),
                Paragraph(f"{r['avg_punctuality']}/5", cell_style),
                Paragraph(f"{r['avg_initiative']}/5", cell_style),
                Paragraph(f"{r['avg_cooperation']}/5", cell_style),
                Paragraph(f"{r['avg_communication']}/5", cell_style),
                Paragraph(f"{r['avg_overall']}/5", cell_style),
            ])

    if has_evals:
        eval_heading = ParagraphStyle('EH', parent=styles['Heading3'],
                                    fontSize=11, textColor=colors.HexColor('#92400e'),
                                    spaceAfter=6)
        elements.append(Paragraph('Performance Evaluation Averages by Office', eval_heading))
        et = Table(eval_data, hAlign='CENTER', repeatRows=1)
        et.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#92400e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fffbeb')]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(et)

    doc.build(elements)
    return buf.getvalue()


@login_required
def director_department_reports_pdf(request):
    if not request.user.is_superuser:
        return redirect('home:home')

    from django.http import HttpResponse
    report, global_stats = _build_department_report_data()
    pdf_bytes = _render_department_report_pdf(report, global_stats)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    date_str = _date.today().strftime('%Y%m%d')
    response['Content-Disposition'] = f'attachment; filename="Department_Reports_{date_str}.pdf"'
    return response


@login_required
def director_department_reports_email(request):
    if not request.user.is_superuser:
        return redirect('home:home')

    from django.core.mail import EmailMessage

    report, global_stats = _build_department_report_data()
    pdf_bytes = _render_department_report_pdf(report, global_stats)

    date_str = _date.today().strftime('%B %d, %Y')
    filename = f"Department_Reports_{_date.today().strftime('%Y%m%d')}.pdf"

    recipient = request.user.email or settings.EMAIL_HOST_USER
    email = EmailMessage(
        subject=f'Department-Level Reports — {date_str}',
        body=(
            f'Good day,\n\n'
            f'Please find attached the Department-Level Reports generated on {date_str}.\n\n'
            f'Summary:\n'
            f'  • Total SAs: {global_stats["total_sas"]}\n'
            f'  • Active SAs: {global_stats["active_sas"]}\n'
            f'  • Attendance Rate: {global_stats["attendance_rate"]}%\n'
            f'  • Avg Hours/SA: {global_stats["avg_hours"]}\n'
            f'  • Avg Performance Rating: {global_stats["avg_overall"]}/5\n'
            f'  • Total Evaluations: {global_stats["eval_count"]}\n\n'
            f'This is an automated report from the CHMSU SA Application System.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    email.attach(filename, pdf_bytes, 'application/pdf')
    email.send()

    messages.success(request, f'Report emailed successfully to {recipient}.')
    return redirect('home:director_department_reports')

def _compute_renewal_recommendation(attendance_rate, total_hours, required_hours, latest_eval):
    att_score = min(attendance_rate, 100)

    hours_pct = (total_hours / required_hours * 100) if required_hours else 0
    hours_score = min(hours_pct, 100)


    if latest_eval and latest_eval.overall_rating:
        perf_score = float(latest_eval.overall_rating) / 5.0 * 100
    else:
        perf_score = None  
    director_recommendation = None
    if latest_eval and latest_eval.recommendation_status:
        director_recommendation = latest_eval.get_recommendation_status_display()

    if perf_score is not None:
        weighted = (att_score * 0.4) + (hours_score * 0.3) + (perf_score * 0.3)
    else:
        weighted = (att_score * 0.6) + (hours_score * 0.4)

    weighted = round(weighted, 1)

    if weighted >= 80:
        recommendation = 'Highly Recommended for Rehire'
        level = 'excellent'
    elif weighted >= 60:
        recommendation = 'Recommended for Rehire'
        level = 'good'
    elif weighted >= 40:
        recommendation = 'Conditional Rehire'
        level = 'conditional'
    else:
        recommendation = 'Not Recommended for Rehire'
        level = 'poor'

    return {
        'recommendation': recommendation,
        'level': level,
        'weighted_score': weighted,
        'attendance_score': round(att_score, 1),
        'hours_score': round(hours_score, 1),
        'performance_score': round(perf_score, 1) if perf_score is not None else None,
        'director_recommendation': director_recommendation,
    }


def sa_completion_certificate(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return redirect('home:home')

    sa = get_object_or_404(
        ActiveStudentAssistant.objects.select_related('assigned_office'), pk=pk
    )

    from django.http import HttpResponse
    from io import BytesIO

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.pdfgen import canvas as pdf_canvas

        buf = BytesIO()
        c = pdf_canvas.Canvas(buf, pagesize=letter)
        width, height = letter

        # Border
        c.setStrokeColor(colors.HexColor('#166534'))
        c.setLineWidth(3)
        c.rect(40, 40, width - 80, height - 80)
        c.setLineWidth(1)
        c.rect(45, 45, width - 90, height - 90)

        # Header
        y = height - 100
        c.setFont('Helvetica-Bold', 22)
        c.setFillColor(colors.HexColor('#14532d'))
        c.drawCentredString(width / 2, y, 'CARLOS HILADO MEMORIAL STATE UNIVERSITY')
        y -= 22
        c.setFont('Helvetica', 12)
        c.setFillColor(colors.HexColor('#166534'))
        c.drawCentredString(width / 2, y, 'Talisay City, Negros Occidental')
        y -= 40
        c.setFont('Helvetica-Bold', 18)
        c.setFillColor(colors.HexColor('#14532d'))
        c.drawCentredString(width / 2, y, 'CERTIFICATE OF COMPLETION')
        y -= 15
        c.setStrokeColor(colors.HexColor('#22c55e'))
        c.setLineWidth(2)
        c.line(width / 2 - 120, y, width / 2 + 120, y)
        y -= 30

        # Body
        c.setFont('Helvetica', 12)
        c.setFillColor(colors.black)
        c.drawCentredString(width / 2, y, 'This is to certify that')
        y -= 35
        c.setFont('Helvetica-Bold', 20)
        c.setFillColor(colors.HexColor('#14532d'))
        c.drawCentredString(width / 2, y, sa.full_name.upper())
        y -= 15
        c.setStrokeColor(colors.HexColor('#14532d'))
        c.setLineWidth(0.5)
        c.line(width / 2 - 150, y, width / 2 + 150, y)
        y -= 25
        c.setFont('Helvetica', 11)
        c.setFillColor(colors.black)
        c.drawCentredString(width / 2, y, f'Student ID: {sa.student_id}    |    Course: {sa.course}')
        y -= 30
        c.setFont('Helvetica', 12)


        office_name = sa.assigned_office.name if sa.assigned_office else 'N/A'
        semester_label = sa.get_semester_display()
        academic_year = sa.academic_year or 'N/A'
        start = sa.start_date.strftime('%B %d, %Y') if sa.start_date else 'N/A'
        end = sa.end_date.strftime('%B %d, %Y') if sa.end_date else 'N/A'
        total_hours = float(sa.total_hours)

        lines = [
            f'has successfully completed the Student Assistant Program',
            f'at {office_name}',
            f'for {semester_label}, A.Y. {academic_year}',
            f'from {start} to {end}',
            f'with a total of {total_hours:.1f} hours rendered.',
        ]
        for line in lines:
            c.drawCentredString(width / 2, y, line)
            y -= 20


        semester_report = _build_semester_report(sa)
        if semester_report:
            y -= 15
            c.setFont('Helvetica', 10)
            c.setFillColor(colors.HexColor('#374151'))
            c.drawCentredString(width / 2, y, f'Attendance Rate: {semester_report["attendance_rate"]}%    |    '
                                f'Present: {semester_report["present"]}    |    Late: {semester_report["late"]}    |    '
                                f'Absent: {semester_report["absent"]}')

        final_eval = sa.evaluations.filter(evaluation_period='final').first()
        if final_eval:
            y -= 25
            c.setFont('Helvetica', 10)
            c.setFillColor(colors.HexColor('#374151'))
            c.drawCentredString(width / 2, y, f'Performance Rating: {final_eval.overall_rating}/5.00')
            if final_eval.recommendation_status:
                y -= 16
                c.drawCentredString(width / 2, y, f'Recommendation: {final_eval.get_recommendation_status_display()}')

        y -= 60
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.5)
        # Left
        c.line(80, y, 260, y)
        c.setFont('Helvetica', 10)
        c.drawCentredString(170, y - 14, 'Student Director')
        # Right
        c.line(width - 260, y, width - 80, y)
        c.drawCentredString(width - 170, y - 14, 'Office Head / Supervisor')

        # Date
        y -= 45
        c.setFont('Helvetica', 10)
        c.setFillColor(colors.HexColor('#6b7280'))
        today_str = _date.today().strftime('%B %d, %Y')
        c.drawCentredString(width / 2, y, f'Issued on: {today_str}')

        c.showPage()
        c.save()
        buf.seek(0)

        response = HttpResponse(buf.getvalue(), content_type='application/pdf')
        safe_name = sa.full_name.replace(' ', '_')
        response['Content-Disposition'] = f'attachment; filename="Completion_Certificate_{safe_name}.pdf"'
        return response

    except ImportError:
        # reportlab not installed — return a simple HTML certificate
        from django.template.loader import render_to_string
        html = f"""<!DOCTYPE html><html><head><title>Completion Certificate</title>
        <style>
        body{{font-family:serif;text-align:center;padding:60px 40px;}}
        h1{{color:#14532d;margin-bottom:5px;}}
        h2{{color:#166534;font-size:1.5rem;margin-top:30px;}}
        .name{{font-size:1.8rem;font-weight:bold;color:#14532d;border-bottom:2px solid #14532d;display:inline-block;padding:0 30px 5px;margin:20px 0;}}
        p{{font-size:1rem;margin:6px 0;}}
        .footer{{margin-top:60px;display:flex;justify-content:space-around;}}
        .sig{{border-top:1px solid #000;width:200px;padding-top:8px;text-align:center;}}
        </style></head><body>
        <h1>CARLOS HILADO MEMORIAL STATE UNIVERSITY</h1>
        <p style="color:#166534;">Talisay City, Negros Occidental</p>
        <h2>CERTIFICATE OF COMPLETION</h2>
        <p>This is to certify that</p>
        <div class="name">{sa.full_name.upper()}</div>
        <p>Student ID: {sa.student_id} | Course: {sa.course}</p>
        <p>has successfully completed the Student Assistant Program</p>
        <p>at {sa.assigned_office.name if sa.assigned_office else 'N/A'}</p>
        <p>for {sa.get_semester_display()}, A.Y. {sa.academic_year or 'N/A'}</p>
        <p>with a total of {float(sa.total_hours):.1f} hours rendered.</p>
        <p style="margin-top:40px;color:#6b7280;">Issued on: {_date.today().strftime('%B %d, %Y')}</p>
        <div class="footer"><div class="sig">Student Director</div><div class="sig">Office Head</div></div>
        </body></html>"""
        return HttpResponse(html)