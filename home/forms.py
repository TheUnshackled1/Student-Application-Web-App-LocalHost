from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import (
    Reminder, UpcomingDate, Announcement, NewApplication, RenewalApplication,
    Office, ActiveStudentAssistant, AttendanceRecord, PerformanceEvaluation,
    StudentProfile, NoDutyDay,
)
import json


# ── Availability schedule choices ──

DAY_CHOICES = [
    ('Monday', 'Monday'),
    ('Tuesday', 'Tuesday'),
    ('Wednesday', 'Wednesday'),
    ('Thursday', 'Thursday'),
    ('Friday', 'Friday'),
    ('Saturday', 'Saturday'),
]

TIME_SLOT_CHOICES = [
    ('7:00 AM - 8:00 AM', '7:00 AM - 8:00 AM'),
    ('8:00 AM - 9:00 AM', '8:00 AM - 9:00 AM'),
    ('9:00 AM - 10:00 AM', '9:00 AM - 10:00 AM'),
    ('10:00 AM - 11:00 AM', '10:00 AM - 11:00 AM'),
    ('11:00 AM - 12:00 PM', '11:00 AM - 12:00 PM'),
    ('12:00 PM - 1:00 PM', '12:00 PM - 1:00 PM'),
    ('1:00 PM - 2:00 PM', '1:00 PM - 2:00 PM'),
    ('2:00 PM - 3:00 PM', '2:00 PM - 3:00 PM'),
    ('3:00 PM - 4:00 PM', '3:00 PM - 4:00 PM'),
    ('4:00 PM - 5:00 PM', '4:00 PM - 5:00 PM'),
    ('5:00 PM - 6:00 PM', '5:00 PM - 6:00 PM'),
]


# ── Shared file validators ──

ALLOWED_DOC_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png')
ALLOWED_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png')
MAX_FILE_SIZE_MB = 10  # 10 MB per document


def validate_file_size(value):
    """Reject files larger than MAX_FILE_SIZE_MB."""
    if value and hasattr(value, 'size') and value.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValidationError(
            f'File size must not exceed {MAX_FILE_SIZE_MB} MB. '
            f'Your file is {value.size / (1024 * 1024):.1f} MB.'
        )


def validate_document_type(value):
    """Only allow PDF and common image types for documents."""
    if value and hasattr(value, 'name'):
        ext = ('.' + value.name.rsplit('.', 1)[-1]).lower() if '.' in value.name else ''
        if ext not in ALLOWED_DOC_EXTENSIONS:
            raise ValidationError(
                f'Unsupported file type "{ext}". Allowed: {", ".join(ALLOWED_DOC_EXTENSIONS)}.'
            )


def validate_image_type(value):
    """Only allow common image types for ID pictures."""
    if value and hasattr(value, 'name'):
        ext = ('.' + value.name.rsplit('.', 1)[-1]).lower() if '.' in value.name else ''
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValidationError(
                f'Unsupported image type "{ext}". Allowed: {", ".join(ALLOWED_IMAGE_EXTENSIONS)}.'
            )


class ReminderForm(forms.ModelForm):
    class Meta:
        model = Reminder
        fields = ['message', 'priority', 'expires_at', 'is_active']
        widgets = {
            'message': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Enter reminder message...',
            }),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'expires_at': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class UpcomingDateForm(forms.ModelForm):
    class Meta:
        model = UpcomingDate
        fields = ['title', 'date', 'expires_at', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Entrance Exam',
            }),
            'date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'expires_at': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'summary', 'image', 'expires_at', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Announcement title...',
            }),
            'summary': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Write the announcement details...',
            }),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'expires_at': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class NewApplicationForm(forms.ModelForm):
    class Meta:
        model = NewApplication
        fields = [
            'first_name', 'middle_initial', 'last_name', 'extension_name',
            'date_of_birth', 'gender', 'contact_number', 'email', 'address',
            'student_id', 'course', 'year_level', 'semester', 'gpa', 'preferred_office',
            'availability_schedule',
            'application_form', 'id_picture', 'barangay_clearance',
            'parents_itr', 'enrolment_form', 'schedule_classes',
            'proof_insurance', 'grades_last_sem', 'official_time',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 15,
                'placeholder': 'Enter first name',
            }),
            'middle_initial': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 1,
                'placeholder': 'M',
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 10,
                'placeholder': 'Enter last name',
            }),
            'extension_name': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 5,
                'placeholder': 'Jr, Sr, III',
            }),
            'date_of_birth': forms.DateInput(attrs={
                'class': 'form-control', 'type': 'date',
            }),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'contact_number': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 11,
                'placeholder': '09XXXXXXXXX', 'inputmode': 'numeric',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'name@example.com',
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 2, 'id': 'id_address',
                'placeholder': 'Home address (click Detect to auto-fill)',
            }),
            'student_id': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 8, 'minlength': 8,
                'placeholder': '12345678', 'inputmode': 'numeric',
                'pattern': '\\d{8}', 'title': 'Student ID must be exactly 8 digits',
            }),
            'course': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. BSIT, BSCS, BEED',
            }),
            'year_level': forms.Select(attrs={'class': 'form-select'}),
            'semester': forms.Select(attrs={'class': 'form-select'}),
            'gpa': forms.NumberInput(attrs={
                'class': 'form-control', 'step': '0.01',
                'min': '1.00', 'max': '5.00',
                'placeholder': 'e.g. 1.75',
            }),
            'preferred_office': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate preferred_office with active offices that still have open slots
        active_offices = Office.objects.filter(is_active=True).order_by('name')
        self.fields['preferred_office'].queryset = active_offices
        self.fields['preferred_office'].empty_label = 'Select preferred office'

    def clean_contact_number(self):
        val = self.cleaned_data['contact_number']
        if not val.isdigit():
            raise forms.ValidationError('Contact number must contain only digits.')
        if len(val) != 11:
            raise forms.ValidationError('Contact number must be exactly 11 digits.')
        return val

    def clean_student_id(self):
        val = self.cleaned_data['student_id']
        if not val.isdigit():
            raise forms.ValidationError('Student ID must contain only digits.')
        if len(val) != 8:
            raise forms.ValidationError('Student ID must be exactly 8 digits.')
        # Block if an active/pending new application exists (allow re-apply after rejected)
        blocking_statuses = ['pending', 'under_review', 'schedule_mismatch', 'documents_requested',
                             'interview_scheduled', 'interview_done', 'office_assigned', 'approved']
        if NewApplication.objects.filter(student_id=val, status__in=blocking_statuses).exists():
            raise forms.ValidationError(
                'You already have an active application. '
                'Please track your existing application or wait until it is completed.'
            )
        # Cross-model: if already approved (new), suggest renewal
        if NewApplication.objects.filter(student_id=val, status='approved').exists():
            raise forms.ValidationError(
                'You already have an approved application. '
                'Please use the Renewal form instead.'
            )
        return val

    def clean_date_of_birth(self):
        from datetime import date
        dob = self.cleaned_data['date_of_birth']
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            raise forms.ValidationError('You must be at least 18 years old to apply. Only college-level students are eligible.')
        return dob

    # ── Document file validators (size & type) ──

    def _validate_doc(self, field_name):
        f = self.cleaned_data.get(field_name)
        if f:
            validate_file_size(f)
            validate_document_type(f)
        return f

    def _validate_img(self, field_name):
        f = self.cleaned_data.get(field_name)
        if f:
            validate_file_size(f)
            validate_image_type(f)
        return f

    def clean_application_form(self):
        return self._validate_doc('application_form')

    def clean_id_picture(self):
        return self._validate_img('id_picture')

    def clean_barangay_clearance(self):
        return self._validate_doc('barangay_clearance')

    def clean_parents_itr(self):
        return self._validate_doc('parents_itr')

    def clean_enrolment_form(self):
        return self._validate_doc('enrolment_form')

    def clean_schedule_classes(self):
        return self._validate_doc('schedule_classes')

    def clean_proof_insurance(self):
        return self._validate_doc('proof_insurance')

    def clean_grades_last_sem(self):
        return self._validate_doc('grades_last_sem')

    def clean_official_time(self):
        return self._validate_doc('official_time')

    def clean_availability_schedule(self):
        data = self.cleaned_data.get('availability_schedule')
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = None
        if not data:
            raise forms.ValidationError('Please select at least one available time slot.')
        return data


class RenewalApplicationForm(forms.ModelForm):
    class Meta:
        model = RenewalApplication
        fields = [
            'student_id', 'full_name', 'email', 'contact_number', 'address',
            'course', 'year_level', 'semester', 'gpa',
            'previous_office', 'preferred_office', 'hours_rendered', 'supervisor_name',
            'availability_schedule',
            'id_picture', 'enrolment_form', 'schedule_classes', 'grades_last_sem',
            'official_time', 'recommendation_letter', 'evaluation_form',
        ]
        widgets = {
            'student_id': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 8, 'minlength': 8,
                'placeholder': '12345678', 'inputmode': 'numeric',
                'pattern': '\\d{8}', 'title': 'Student ID must be exactly 8 digits',
            }),
            'full_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Juan A. Dela Cruz',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'name@example.com',
            }),
            'contact_number': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 11,
                'placeholder': '09XXXXXXXXX', 'inputmode': 'numeric',
            }),
            'address': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Street, Barangay, City / Municipality, Province',
            }),
            'course': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. BSIT, BSCS, BEED',
            }),
            'year_level': forms.Select(attrs={'class': 'form-select'}),
            'semester': forms.Select(attrs={'class': 'form-select'}),
            'gpa': forms.NumberInput(attrs={
                'class': 'form-control', 'step': '0.01',
                'min': '1.00', 'max': '5.00',
                'placeholder': 'e.g. 1.75',
            }),
            'previous_office': forms.Select(attrs={'class': 'form-select'}),
            'preferred_office': forms.Select(attrs={'class': 'form-select'}),
            'hours_rendered': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 120',
            }),
            'supervisor_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Full name of your previous supervisor',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_offices = Office.objects.filter(is_active=True).order_by('name')
        self.fields['previous_office'].queryset = active_offices
        self.fields['previous_office'].empty_label = 'Select office'
        self.fields['preferred_office'].queryset = active_offices
        self.fields['preferred_office'].empty_label = 'Select office'

    def clean_contact_number(self):
        val = self.cleaned_data['contact_number']
        if not val.isdigit():
            raise forms.ValidationError('Contact number must contain only digits.')
        if len(val) != 11:
            raise forms.ValidationError('Contact number must be exactly 11 digits.')
        return val

    def clean_student_id(self):
        val = self.cleaned_data['student_id']
        if not val.isdigit():
            raise forms.ValidationError('Student ID must contain only digits.')
        if len(val) != 8:
            raise forms.ValidationError('Student ID must be exactly 8 digits.')
        # Block if an active/pending renewal exists
        blocking_statuses = ['pending', 'under_review', 'schedule_mismatch', 'documents_requested',
                             'interview_scheduled', 'interview_done', 'office_assigned', 'approved']
        if RenewalApplication.objects.filter(student_id=val, status__in=blocking_statuses).exists():
            raise forms.ValidationError(
                'You already have an active renewal application. '
                'Please track your existing application or wait until it is completed.'
            )
        return val

    # ── Document file validators (size & type) ──

    def _validate_doc(self, field_name):
        f = self.cleaned_data.get(field_name)
        if f:
            validate_file_size(f)
            validate_document_type(f)
        return f

    def _validate_img(self, field_name):
        f = self.cleaned_data.get(field_name)
        if f:
            validate_file_size(f)
            validate_image_type(f)
        return f

    def clean_id_picture(self):
        return self._validate_img('id_picture')

    def clean_enrolment_form(self):
        return self._validate_doc('enrolment_form')

    def clean_schedule_classes(self):
        return self._validate_doc('schedule_classes')

    def clean_grades_last_sem(self):
        return self._validate_doc('grades_last_sem')

    def clean_official_time(self):
        return self._validate_doc('official_time')

    def clean_recommendation_letter(self):
        return self._validate_doc('recommendation_letter')

    def clean_evaluation_form(self):
        return self._validate_doc('evaluation_form')

    def clean_availability_schedule(self):
        data = self.cleaned_data.get('availability_schedule')
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = None
        if not data:
            raise forms.ValidationError('Please select at least one available time slot.')
        return data


# ================================================================
#  SCHEDULE RESUBMISSION FORM
# ================================================================

class ScheduleResubmitForm(forms.Form):
    """Form for students to re-submit their availability schedule."""
    availability_schedule = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_availability_schedule'}),
        required=True,
    )

    def clean_availability_schedule(self):
        data = self.cleaned_data.get('availability_schedule', '{}')
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = None
        if not data:
            raise forms.ValidationError('Please select at least one available time slot.')
        return data


# ================================================================
#  DOCUMENT RESUBMISSION FORM
# ================================================================

class DocumentResubmitForm(forms.Form):
    """Form for students to re-upload documents requested by staff."""
    application_form = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    id_picture = forms.ImageField(required=False, validators=[validate_file_size, validate_image_type],
                                   widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.jpg,.jpeg,.png'}))
    barangay_clearance = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                          widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    parents_itr = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                    widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    enrolment_form = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                      widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    schedule_classes = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    proof_insurance = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                       widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    grades_last_sem = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                       widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    official_time = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                     widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    recommendation_letter = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                             widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))
    evaluation_form = forms.FileField(required=False, validators=[validate_file_size, validate_document_type],
                                       widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}))


# ================================================================
#  OFFICE FORM
# ================================================================

ICON_CHOICES = [
    ('fa-solid fa-building', 'Building'),
    ('fa-solid fa-book', 'Book / Library'),
    ('fa-solid fa-user-tie', 'User / Office Head'),
    ('fa-solid fa-gavel', 'Gavel / Dean'),
    ('fa-solid fa-calculator', 'Calculator / Accounting'),
    ('fa-solid fa-cash-register', 'Cash Register / Cashier'),
    ('fa-solid fa-users', 'Users / Student Affairs'),
    ('fa-solid fa-laptop-code', 'Laptop / ICT'),
    ('fa-solid fa-flask', 'Flask / Research'),
    ('fa-solid fa-id-card', 'ID Card / HR'),
    ('fa-solid fa-hand-holding-heart', 'Heart / Guidance'),
    ('fa-solid fa-graduation-cap', 'Grad Cap / Academic'),
    ('fa-solid fa-clipboard-list', 'Clipboard / Registrar'),
    ('fa-solid fa-shield-halved', 'Shield / Security'),
    ('fa-solid fa-stethoscope', 'Stethoscope / Clinic'),
    ('fa-solid fa-tools', 'Tools / Maintenance'),
]


class OfficeForm(forms.ModelForm):
    class Meta:
        model = Office
        fields = [
            'name', 'building', 'room', 'hours', 'head',
            'total_slots', 'latitude', 'longitude', 'icon', 'description', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Registrar\u2019s Office',
            }),
            'building': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Administration Building',
            }),
            'room': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Ground Floor, Room 101',
            }),
            'hours': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Mon\u2013Fri, 8:00 AM \u2013 5:00 PM',
            }),
            'head': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Head / Supervisor name',
            }),
            'total_slots': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '1', 'max': '50',
                'placeholder': '3',
            }),
            'latitude': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.00001', 'id': 'id_latitude',
            }),
            'longitude': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.00001', 'id': 'id_longitude',
            }),
            'icon': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Brief description of the office\u2026',
            }),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['icon'].choices = ICON_CHOICES
        # Pre-fill model defaults so add-form shows real values (not just placeholders)
        if not self.instance.pk:
            self.initial.setdefault('hours', 'Mon\u2013Fri, 8:00 AM \u2013 5:00 PM')
            self.initial.setdefault('total_slots', 3)
            self.initial.setdefault('is_active', True)


# ================================================================
#  ACTIVE SA MANAGEMENT FORMS
# ================================================================

class AttendanceForm(forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = ['date', 'time_in', 'time_out', 'status', 'remarks']
        widgets = {
            'date': forms.DateInput(attrs={
                'class': 'form-control', 'type': 'date',
            }),
            'time_in': forms.TimeInput(attrs={
                'class': 'form-control', 'type': 'time',
            }),
            'time_out': forms.TimeInput(attrs={
                'class': 'form-control', 'type': 'time',
            }),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'remarks': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 2,
                'placeholder': 'Optional remarks...',
            }),
        }


class PerformanceEvaluationForm(forms.ModelForm):
    class Meta:
        model = PerformanceEvaluation
        fields = [
            'evaluation_period', 'work_quality', 'punctuality',
            'initiative', 'cooperation', 'communication', 'remarks',
        ]
        widgets = {
            'evaluation_period': forms.Select(attrs={'class': 'form-select'}),
            'work_quality': forms.NumberInput(attrs={
                'class': 'form-control', 'min': '1', 'max': '5',
            }),
            'punctuality': forms.NumberInput(attrs={
                'class': 'form-control', 'min': '1', 'max': '5',
            }),
            'initiative': forms.NumberInput(attrs={
                'class': 'form-control', 'min': '1', 'max': '5',
            }),
            'cooperation': forms.NumberInput(attrs={
                'class': 'form-control', 'min': '1', 'max': '5',
            }),
            'communication': forms.NumberInput(attrs={
                'class': 'form-control', 'min': '1', 'max': '5',
            }),
            'remarks': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': 'Additional comments or recommendations...',
            }),
        }

    def clean(self):
        cleaned = super().clean()
        for field in ['work_quality', 'punctuality', 'initiative', 'cooperation', 'communication']:
            val = cleaned.get(field)
            if val is not None and (val < 1 or val > 5):
                self.add_error(field, 'Rating must be between 1 and 5.')
        return cleaned


class ActiveSAStatusForm(forms.ModelForm):
    class Meta:
        model = ActiveStudentAssistant
        fields = ['status', 'end_date', 'required_hours']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'end_date': forms.DateInput(attrs={
                'class': 'form-control', 'type': 'date',
            }),
            'required_hours': forms.NumberInput(attrs={
                'class': 'form-control', 'min': '1',
            }),
        }


# ================================================================
#  STUDENT REGISTRATION & LOGIN FORMS
# ================================================================

class StudentRegistrationForm(forms.Form):
    """Registration form for students."""
    student_id = forms.CharField(
        max_length=8, min_length=8,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': '12345678',
            'inputmode': 'numeric', 'pattern': r'\d{8}',
            'title': 'Student ID must be exactly 8 digits',
        }),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-control', 'placeholder': 'name@example.com',
        }),
    )
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'First name',
        }),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Last name',
        }),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'placeholder': 'Password',
        }),
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'placeholder': 'Confirm password',
        }),
    )

    def clean_student_id(self):
        val = self.cleaned_data['student_id']
        if not val.isdigit():
            raise ValidationError('Student ID must contain only digits.')
        if StudentProfile.objects.filter(student_id=val).exists():
            raise ValidationError('An account with this Student ID already exists.')
        return val

    def clean_email(self):
        val = self.cleaned_data['email']
        if User.objects.filter(email=val).exists():
            raise ValidationError('An account with this email already exists.')
        return val

    def clean_password(self):
        val = self.cleaned_data['password']
        validate_password(val)
        return val

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get('password')
        pw2 = cleaned.get('password_confirm')
        if pw and pw2 and pw != pw2:
            self.add_error('password_confirm', 'Passwords do not match.')
        return cleaned


class StudentLoginForm(forms.Form):
    """Login form using student_id + password."""
    student_id = forms.CharField(
        max_length=8, min_length=8,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'Student ID (8 digits)',
            'inputmode': 'numeric',
        }),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'placeholder': 'Password',
        }),
    )


# ================================================================
#  NO-DUTY DAY FORM
# ================================================================

class NoDutyDayForm(forms.ModelForm):
    class Meta:
        model = NoDutyDay
        fields = ['date', 'reason', 'office']
        widgets = {
            'date': forms.DateInput(attrs={
                'class': 'form-control', 'type': 'date',
            }),
            'reason': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Holiday, Office Closed',
            }),
            'office': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['office'].queryset = Office.objects.filter(is_active=True).order_by('name')
        self.fields['office'].empty_label = 'All Offices'
        self.fields['office'].required = False