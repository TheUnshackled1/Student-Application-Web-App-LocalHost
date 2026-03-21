
import os

from django.core.mail import EmailMultiAlternatives
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def _html_wrap(body_html):
    """Wrap body content in a styled HTML email template with Gmail annotation."""
    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background:#f4f6f8; font-family:'Segoe UI',Roboto,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8; padding:24px 0;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
  <tr>
    <td style="background:linear-gradient(135deg,#059669,#10b981); padding:24px 32px; text-align:center;">
      <h1 style="margin:0; font-size:20px; color:#ffffff; font-weight:700; letter-spacing:0.5px;">
        📋 SWA Application System
      </h1>
      <p style="margin:4px 0 0; font-size:12px; color:rgba(255,255,255,0.8);">Carlos Hilado Memorial State University</p>
    </td>
  </tr>
  <tr>
    <td style="padding:28px 32px 32px;">
      {body_html}
      <hr style="border:none; border-top:1px solid #e5e7eb; margin:24px 0 16px;">
      <p style="margin:0; font-size:12px; color:#9ca3af; text-align:center;">
        This is an automated message from the SWA Application System.<br>
        Please do not reply to this email.
      </p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _get_status_display(application, status_key):
    """Get human-readable status label from choices."""
    return dict(application.STATUS_CHOICES).get(status_key, status_key)


def send_application_confirmation(application, app_type='new'):
    if not application.email:
        logger.warning('No email for %s, skipping confirmation.', application.student_id)
        return False

    type_label = 'New Application' if app_type == 'new' else 'Renewal Application'
    subject = f'SWA Application Received — {application.student_id}'
    name = _applicant_name(application)

    plain = (
        f"Dear {name},\n\n"
        f"Thank you for submitting your {type_label} for the Student Assistant program.\n\n"
        f"Application Details:\n"
        f"  • Student ID : {application.student_id}\n"
        f"  • Type       : {type_label}\n"
        f"  • Status     : Pending\n\n"
        f"Your application is now under review. You will receive email updates "
        f"whenever the status changes.\n\n"
        f"— SWA Application System"
    )

    badge_color = '#3b82f6' if app_type == 'new' else '#8b5cf6'
    html_body = f"""\
      <p style="margin:0 0 16px; font-size:15px; color:#1e293b;">Dear <strong>{name}</strong>,</p>
      <p style="margin:0 0 20px; font-size:14px; color:#475569; line-height:1.6;">
        Thank you for submitting your <strong>{type_label}</strong> for the Student Assistant program.
      </p>
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:20px;">
        <tr>
          <td style="padding:16px 20px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="font-size:13px; color:#64748b; padding:4px 0;">Student ID</td>
                <td style="font-size:13px; color:#0f172a; font-weight:600; text-align:right; padding:4px 0;">{application.student_id}</td>
              </tr>
              <tr>
                <td style="font-size:13px; color:#64748b; padding:4px 0;">Type</td>
                <td style="font-size:13px; text-align:right; padding:4px 0;">
                  <span style="background:{badge_color}; color:#fff; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600;">{type_label}</span>
                </td>
              </tr>
              <tr>
                <td style="font-size:13px; color:#64748b; padding:4px 0;">Status</td>
                <td style="font-size:13px; text-align:right; padding:4px 0;">
                  <span style="background:#fef3c7; color:#92400e; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600;">⏳ Pending</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
      <p style="margin:0; font-size:14px; color:#475569; line-height:1.6;">
        Your application is now under review. You will receive email updates whenever the status changes.
      </p>"""

    return _send(subject, plain, _html_wrap(html_body), application.email)


def send_status_update_email(application, old_status, new_status, extra_message=''):
    if not application.email:
        return False

    new_label = _get_status_display(application, new_status)
    old_label = _get_status_display(application, old_status)
    name = _applicant_name(application)
    subject = f'SWA Application Update — {new_label}'

    body_map = {
        'under_review':
            'Your application is now being reviewed by our staff.',
        'schedule_mismatch':
            'We found a mismatch between your availability schedule and your '
            'uploaded Schedule of Classes. Please log in and re-submit your '
            'availability schedule.',
        'documents_requested':
            f'Additional documents are required for your application.\n\n'
            f'Details from staff:\n{extra_message}',
        'interview_scheduled':
            f'An interview has been scheduled for your application.\n{extra_message}',
        'interview_done':
            'Your interview has been completed. We will notify you of the next steps.',
        'office_assigned':
            'You have been assigned to an office. Check your application for details.',
        'approved':
            'Congratulations! Your application has been APPROVED. '
            'Welcome to the Student Assistant program!',
        'rejected':
            f'We regret to inform you that your application has been rejected.\n{extra_message}',
    }
    status_msg = body_map.get(new_status, f'Your status has been updated to: {new_label}')

    plain = (
        f"Dear {name},\n\n"
        f"Your SWA application status has been updated.\n\n"
        f"  • Student ID      : {application.student_id}\n"
        f"  • Previous Status  : {old_label}\n"
        f"  • New Status       : {new_label}\n\n"
        f"{status_msg}\n\n"
        f"— SWA Application System"
    )

    # Status color mapping
    color_map = {
        'under_review': ('#dbeafe', '#1e40af', '🔍'),
        'schedule_mismatch': ('#fef3c7', '#92400e', '⚠️'),
        'documents_requested': ('#fef3c7', '#92400e', '📄'),
        'interview_scheduled': ('#dbeafe', '#1e40af', '📅'),
        'interview_done': ('#d1fae5', '#065f46', '✅'),
        'office_assigned': ('#d1fae5', '#065f46', '🏢'),
        'approved': ('#d1fae5', '#065f46', '🎉'),
        'rejected': ('#fee2e2', '#991b1b', '❌'),
    }
    bg, fg, icon = color_map.get(new_status, ('#f1f5f9', '#334155', '📋'))
    extra_html = ''
    if extra_message:
        safe_msg = extra_message.replace('\n', '<br>')
        extra_html = f'<div style="background:#f8fafc; border-left:3px solid #059669; padding:12px 16px; margin-top:16px; border-radius:0 6px 6px 0; font-size:13px; color:#475569; line-height:1.5;">{safe_msg}</div>'

    html_body = f"""\
      <p style="margin:0 0 16px; font-size:15px; color:#1e293b;">Dear <strong>{name}</strong>,</p>
      <p style="margin:0 0 20px; font-size:14px; color:#475569; line-height:1.6;">
        Your SWA application status has been updated.
      </p>
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:20px;">
        <tr>
          <td style="padding:16px 20px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="font-size:13px; color:#64748b; padding:4px 0;">Student ID</td>
                <td style="font-size:13px; color:#0f172a; font-weight:600; text-align:right; padding:4px 0;">{application.student_id}</td>
              </tr>
              <tr>
                <td style="font-size:13px; color:#64748b; padding:4px 0;">Previous</td>
                <td style="font-size:13px; color:#64748b; text-align:right; padding:4px 0;">{old_label}</td>
              </tr>
              <tr>
                <td style="font-size:13px; color:#64748b; padding:4px 0;">New Status</td>
                <td style="font-size:13px; text-align:right; padding:4px 0;">
                  <span style="background:{bg}; color:{fg}; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600;">{icon} {new_label}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
      <p style="margin:0; font-size:14px; color:#475569; line-height:1.6;">
        {status_msg.replace(chr(10), '<br>')}
      </p>
      {extra_html}"""

    return _send(subject, plain, _html_wrap(html_body), application.email)


def send_schedule_mismatch_email(application, mismatch_note):
    """Convenience wrapper for schedule-mismatch notification."""
    return send_status_update_email(
        application, application.status, 'schedule_mismatch',
        extra_message=mismatch_note,
    )


def send_document_request_email(application, requested_docs_note):
    """Convenience wrapper for document-request notification."""
    return send_status_update_email(
        application, application.status, 'documents_requested',
        extra_message=requested_docs_note,
    )


# ── internal helpers ──

def _applicant_name(application):
    """Return a display name for any application type."""
    if hasattr(application, 'first_name'):
        return f"{application.first_name} {application.last_name}"
    return application.full_name


def _send(subject, plain_message, html_message, recipient):
    """Send HTML email with plain-text fallback and priority/notification headers."""
    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        email.attach_alternative(html_message, 'text/html')
        # Priority headers for push notifications on mobile/desktop clients
        email.extra_headers = {
            'X-Priority': '1',
            'X-MSMail-Priority': 'High',
            'Importance': 'High',
            'X-Mailer': 'SWA-Application-System',
            # Helps Gmail categorize as Primary (not Promotions/Updates)
            'Reply-To': settings.DEFAULT_FROM_EMAIL,
        }
        email.send(fail_silently=False)
        logger.info('Email sent to %s: %s', recipient, subject)
        return True
    except Exception as e:
        logger.error('Failed to send email to %s: %s', recipient, e)
        return False


def send_verification_email(user, request=None):
    """Send an email-verification link after student registration."""
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    if request:
        base = request.build_absolute_uri('/')[:-1]
    else:
        base = os.environ.get('SITE_URL', 'http://localhost:8000').rstrip('/')

    link = f"{base}/verify-email/{uid}/{token}/"
    subject = 'SWA Application — Verify Your Email'
    name = f"{user.first_name} {user.last_name}"
    plain = (
        f"Dear {name},\n\n"
        f"Thank you for registering on the SWA Application System.\n\n"
        f"Please verify your email by clicking the link below:\n"
        f"  {link}\n\n"
        f"If you did not create this account, you can ignore this email.\n\n"
        f"— SWA Application System"
    )
    html_body = f"""\
      <p style="margin:0 0 16px; font-size:15px; color:#1e293b;">Dear <strong>{name}</strong>,</p>
      <p style="margin:0 0 20px; font-size:14px; color:#475569; line-height:1.6;">
        Thank you for registering on the SWA Application System. Please verify your email address.
      </p>
      <div style="text-align:center; margin:24px 0;">
        <a href="{link}" style="display:inline-block; padding:12px 32px; background:linear-gradient(135deg,#059669,#10b981); color:#fff; text-decoration:none; border-radius:8px; font-weight:600; font-size:14px;">
          ✅ Verify Email Address
        </a>
      </div>
      <p style="margin:0; font-size:12px; color:#94a3b8; line-height:1.5;">
        If the button doesn't work, copy and paste this link:<br>
        <a href="{link}" style="color:#059669; word-break:break-all;">{link}</a>
      </p>"""
    return _send(subject, plain, _html_wrap(html_body), user.email)


# ================================================================
#  DUTY NOTIFICATIONS
# ================================================================

def _duty_html(name, rows, note=''):
    """Build HTML body for duty-related emails."""
    rows_html = ''
    for label, value in rows:
        rows_html += f'<tr><td style="font-size:13px; color:#64748b; padding:4px 0;">{label}</td><td style="font-size:13px; color:#0f172a; font-weight:600; text-align:right; padding:4px 0;">{value}</td></tr>'
    note_html = f'<p style="margin:16px 0 0; font-size:14px; color:#475569; line-height:1.6;">{note}</p>' if note else ''
    return f"""\
      <p style="margin:0 0 16px; font-size:15px; color:#1e293b;">Dear <strong>{name}</strong>,</p>
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:16px;">
        <tr><td style="padding:16px 20px;"><table width="100%" cellpadding="0" cellspacing="0">{rows_html}</table></td></tr>
      </table>
      {note_html}"""


def send_shift_reminder_email(sa, shift_label):
    """Send a reminder email 5 minutes before a scheduled shift."""
    if not sa.email:
        logger.warning('No email for SA %s, skipping shift reminder.', sa.student_id)
        return False

    office_name = sa.assigned_office.name if sa.assigned_office else 'your assigned office'
    subject = f'Duty Reminder — {shift_label}'
    plain = (
        f"Dear {sa.full_name},\n\n"
        f"This is a friendly reminder that your duty shift is about to start.\n\n"
        f"  • Shift      : {shift_label}\n"
        f"  • Office     : {office_name}\n"
        f"  • Date       : Today\n\n"
        f"Please make sure to clock in on time via the Student Dashboard.\n\n"
        f"— SWA Application System"
    )
    html_body = _duty_html(sa.full_name, [
        ('Shift', shift_label), ('Office', office_name), ('Date', 'Today'),
    ], 'This is a friendly reminder that your duty shift is about to start. Please clock in on time.')
    return _send(subject, plain, _html_wrap(html_body), sa.email)


def send_absent_notification_email(sa, absent_date, shift_label):
    """Send a notification when a student is marked absent for a shift."""
    if not sa.email:
        logger.warning('No email for SA %s, skipping absent notification.', sa.student_id)
        return False

    office_name = sa.assigned_office.name if sa.assigned_office else 'your assigned office'
    date_str = absent_date.strftime('%B %d, %Y')
    subject = f'Absent Notice — {date_str}'
    plain = (
        f"Dear {sa.full_name},\n\n"
        f"You have been marked ABSENT for the following shift:\n\n"
        f"  • Date       : {date_str}\n"
        f"  • Shift      : {shift_label}\n"
        f"  • Office     : {office_name}\n\n"
        f"If you believe this is an error, please contact your office head "
        f"or the SWA staff to request an excuse.\n\n"
        f"— SWA Application System"
    )
    html_body = _duty_html(sa.full_name, [
        ('Date', date_str), ('Shift', shift_label), ('Office', office_name),
        ('Status', '<span style="background:#fee2e2; color:#991b1b; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600;">❌ Absent</span>'),
    ], 'If you believe this is an error, please contact your office head or the SWA staff to request an excuse.')
    return _send(subject, plain, _html_wrap(html_body), sa.email)


def send_consecutive_absence_alert(sa, streak_count, recent_dates):
    """Alert when a student has consecutive absences."""
    if not sa.email:
        return False

    office_name = sa.assigned_office.name if sa.assigned_office else 'your assigned office'
    dates_str = ', '.join(d.strftime('%B %d') for d in recent_dates)
    subject = f'Attendance Warning — {streak_count} Consecutive Absences'
    plain = (
        f"Dear {sa.full_name},\n\n"
        f"You have been absent for {streak_count} consecutive duty day(s).\n\n"
        f"  • Office     : {office_name}\n"
        f"  • Dates      : {dates_str}\n\n"
        f"Consistent attendance is required for all Student Assistants.\n"
        f"Please report to your assigned office or contact the SWA staff "
        f"if you have a valid reason for your absences.\n\n"
        f"— SWA Application System"
    )
    html_body = _duty_html(sa.full_name, [
        ('Office', office_name), ('Absences', f'{streak_count} consecutive days'), ('Dates', dates_str),
    ], '⚠️ Consistent attendance is required. Please report to your assigned office or contact the SWA staff if you have a valid reason.')
    return _send(subject, plain, _html_wrap(html_body), sa.email)


def send_late_threshold_alert(sa, late_count, month_label):
    """Alert when a student exceeds the monthly late threshold."""
    if not sa.email:
        return False

    office_name = sa.assigned_office.name if sa.assigned_office else 'your assigned office'
    subject = f'Late Attendance Warning — {late_count} Late Records in {month_label}'
    plain = (
        f"Dear {sa.full_name},\n\n"
        f"You have been marked LATE {late_count} time(s) this month ({month_label}).\n\n"
        f"  • Office     : {office_name}\n"
        f"  • Late Count : {late_count}\n\n"
        f"Please make an effort to clock in on time. Excessive tardiness "
        f"may affect your standing as a Student Assistant.\n\n"
        f"— SWA Application System"
    )
    html_body = _duty_html(sa.full_name, [
        ('Office', office_name), ('Month', month_label),
        ('Late Count', f'<span style="background:#fef3c7; color:#92400e; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600;">⚠️ {late_count} times</span>'),
    ], 'Please make an effort to clock in on time. Excessive tardiness may affect your standing as a Student Assistant.')
    return _send(subject, plain, _html_wrap(html_body), sa.email)
