from django.db import models
from django.contrib.auth.models import User


class Office(models.Model):
    """Campus office that can accept student assistants."""
    name = models.CharField(max_length=200, unique=True)
    building = models.CharField(max_length=200)
    room = models.CharField(max_length=200, blank=True, default='')
    hours = models.CharField(max_length=200, default='Mon–Fri, 8:00 AM – 5:00 PM')
    head = models.CharField(max_length=200, blank=True, default='')
    total_slots = models.PositiveIntegerField(default=3)
    latitude = models.FloatField(default=10.7426)
    longitude = models.FloatField(default=122.9703)
    icon = models.CharField(max_length=100, default='fa-solid fa-building')
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class StudentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    application_id = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} ({self.application_id})"


class Document(models.Model):
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('missing', 'Missing'),
    ]

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    uploaded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"


class ApplicationStep(models.Model):
    STEP_STATUS_CHOICES = [
        ('done', 'Done'),
        ('current', 'Current'),
        ('locked', 'Locked'),
    ]

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='application_steps')
    step_number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STEP_STATUS_CHOICES, default='locked')

    class Meta:
        ordering = ['step_number']

    def __str__(self):
        return f"Step {self.step_number}: {self.title} ({self.get_status_display()})"


class UpcomingDate(models.Model):
    title = models.CharField(max_length=200)
    date = models.DateField()
    is_active = models.BooleanField(default=True)
    expires_at = models.DateField(null=True, blank=True, help_text='Content will hide from homepage after this date.')

    class Meta:
        ordering = ['date']

    @property
    def is_expired(self):
        from datetime import date as _date
        return self.expires_at is not None and self.expires_at < _date.today()

    def __str__(self):
        return f"{self.title} - {self.date}"


class Reminder(models.Model):
    PRIORITY_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('urgent', 'Urgent'),
    ]

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='reminders', null=True, blank=True)
    message = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='info')
    is_active = models.BooleanField(default=True)
    expires_at = models.DateField(null=True, blank=True, help_text='Content will hide from homepage after this date.')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_expired(self):
        from datetime import date as _date
        return self.expires_at is not None and self.expires_at < _date.today()

    def __str__(self):
        return f"[{self.get_priority_display()}] {self.message[:50]}"


class Announcement(models.Model):
    title = models.CharField(max_length=300)
    summary = models.TextField()
    image = models.ImageField(upload_to='announcements/', null=True, blank=True)
    published_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateField(null=True, blank=True, help_text='Content will hide from homepage after this date.')

    class Meta:
        ordering = ['-published_at']

    @property
    def is_expired(self):
        from datetime import date as _date
        return self.expires_at is not None and self.expires_at < _date.today()

    def __str__(self):
        return self.title


class NewApplication(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]

    YEAR_LEVEL_CHOICES = [
        (1, '1st Year'),
        (2, '2nd Year'),
        (3, '3rd Year'),
        (4, '4th Year'),
    ]

    SEMESTER_CHOICES = [
        ('1st', '1st Semester'),
        ('2nd', '2nd Semester'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('under_review', 'Under Review'),
        ('schedule_mismatch', 'Schedule Mismatch — Re-input Required'),
        ('documents_requested', 'Additional Documents Requested'),
        ('interview_scheduled', 'Interview Scheduled'),
        ('interview_done', 'Interview Done'),
        ('office_assigned', 'Office Assigned'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    # ── Personal Information ──
    first_name = models.CharField(max_length=15)
    middle_initial = models.CharField(max_length=1)
    last_name = models.CharField(max_length=10)
    extension_name = models.CharField(max_length=5, blank=True, default='')
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    contact_number = models.CharField(max_length=11)
    email = models.EmailField()
    address = models.TextField()

    # ── Academic Information ──
    student_id = models.CharField(max_length=8, unique=True)
    course = models.CharField(max_length=100)
    year_level = models.IntegerField(choices=YEAR_LEVEL_CHOICES)
    semester = models.CharField(max_length=5, choices=SEMESTER_CHOICES)

    # ── Document Uploads ──
    application_form = models.FileField(upload_to='applications/new/', blank=True)
    id_picture = models.ImageField(upload_to='applications/new/', blank=True)
    barangay_clearance = models.FileField(upload_to='applications/new/', blank=True)
    parents_itr = models.FileField(upload_to='applications/new/', blank=True)
    enrolment_form = models.FileField(upload_to='applications/new/', blank=True)
    schedule_classes = models.FileField(upload_to='applications/new/', blank=True)
    proof_insurance = models.FileField(upload_to='applications/new/', blank=True)
    grades_last_sem = models.FileField(upload_to='applications/new/', blank=True)
    official_time = models.FileField(upload_to='applications/new/', blank=True)

    # ── Preferred Office ──
    preferred_office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='new_applications',
        help_text='Office the student prefers to be assigned to.',
    )

    # ── Availability Schedule ──
    availability_schedule = models.JSONField(
        blank=True, null=True,
        help_text='Student available days/time slots as {"Monday": ["8:00 AM - 9:00 AM", ...], ...}',
    )
    schedule_verified = models.BooleanField(
        default=False,
        help_text='Staff has verified schedule matches uploaded Schedule of Classes.',
    )
    schedule_mismatch_note = models.TextField(
        blank=True, default='',
        help_text='Staff note explaining schedule mismatch.',
    )

    # ── Notes & Internal Remarks ──
    staff_notes = models.TextField(blank=True, default='', help_text='Internal notes from staff.')
    director_notes = models.TextField(blank=True, default='', help_text='Internal notes from director.')

    # ── Request Additional Documents ──
    requested_documents_note = models.TextField(
        blank=True, default='',
        help_text='Description of which additional documents are needed from the student.',
    )

    # ── Workflow / Scheduling ──
    interview_date = models.DateTimeField(null=True, blank=True)
    assigned_office = models.CharField(max_length=200, blank=True, default='')
    start_date = models.DateField(null=True, blank=True)

    # ── Meta ──
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending')

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.student_id})"


class RenewalApplication(models.Model):
    """Renewal application for returning student assistants."""

    YEAR_LEVEL_CHOICES = NewApplication.YEAR_LEVEL_CHOICES
    SEMESTER_CHOICES = NewApplication.SEMESTER_CHOICES
    STATUS_CHOICES = NewApplication.STATUS_CHOICES

    # ── Identity ──
    student_id = models.CharField(max_length=8, unique=True)
    full_name = models.CharField(max_length=200)
    email = models.EmailField()
    contact_number = models.CharField(max_length=11)
    address = models.TextField()

    # ── Academic ──
    course = models.CharField(max_length=100)
    year_level = models.IntegerField(choices=YEAR_LEVEL_CHOICES)
    semester = models.CharField(max_length=5, choices=SEMESTER_CHOICES)

    # ── Previous & Preferred Assignment ──
    previous_office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='renewal_previous',
        help_text='Office where the student previously served.',
    )
    preferred_office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='renewal_preferred',
        help_text='Office the student prefers for renewal.',
    )
    hours_rendered = models.PositiveIntegerField()
    supervisor_name = models.CharField(max_length=200, blank=True, default='')

    # ── Renewal Documents ──
    id_picture = models.ImageField(upload_to='applications/renewal/', blank=True)
    enrolment_form = models.FileField(upload_to='applications/renewal/', blank=True)
    schedule_classes = models.FileField(upload_to='applications/renewal/', blank=True)
    grades_last_sem = models.FileField(upload_to='applications/renewal/', blank=True)
    official_time = models.FileField(upload_to='applications/renewal/', blank=True)
    recommendation_letter = models.FileField(upload_to='applications/renewal/', blank=True)
    evaluation_form = models.FileField(upload_to='applications/renewal/', blank=True)

    # ── Availability Schedule ──
    availability_schedule = models.JSONField(
        blank=True, null=True,
        help_text='Student available days/time slots as {"Monday": ["8:00 AM - 9:00 AM", ...], ...}',
    )
    schedule_verified = models.BooleanField(
        default=False,
        help_text='Staff has verified schedule matches uploaded Schedule of Classes.',
    )
    schedule_mismatch_note = models.TextField(
        blank=True, default='',
        help_text='Staff note explaining schedule mismatch.',
    )

    # ── Notes & Internal Remarks ──
    staff_notes = models.TextField(blank=True, default='', help_text='Internal notes from staff.')
    director_notes = models.TextField(blank=True, default='', help_text='Internal notes from director.')

    # ── Request Additional Documents ──
    requested_documents_note = models.TextField(
        blank=True, default='',
        help_text='Description of which additional documents are needed from the student.',
    )

    # ── Workflow / Scheduling ──
    interview_date = models.DateTimeField(null=True, blank=True)
    assigned_office = models.CharField(max_length=200, blank=True, default='')
    start_date = models.DateField(null=True, blank=True)

    # ── Meta ──
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending')

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"[Renewal] {self.full_name} ({self.student_id})"


class ApplicationNote(models.Model):
    """Audit-trail log of all notes, remarks, and status changes."""

    NOTE_TYPE_CHOICES = [
        ('staff', 'Staff Note'),
        ('director', 'Director Note'),
        ('schedule_mismatch', 'Schedule Mismatch'),
        ('document_request', 'Document Request'),
        ('status_change', 'Status Change'),
    ]

    new_application = models.ForeignKey(
        NewApplication, null=True, blank=True, on_delete=models.CASCADE,
        related_name='notes_log',
    )
    renewal_application = models.ForeignKey(
        RenewalApplication, null=True, blank=True, on_delete=models.CASCADE,
        related_name='notes_log',
    )
    author = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
    )
    note_type = models.CharField(max_length=30, choices=NOTE_TYPE_CHOICES, default='staff')
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        app = self.new_application or self.renewal_application
        return f"{self.get_note_type_display()} by {self.author} on {app}"


# ================================================================
#  Active Student Assistant Management
# ================================================================

class ActiveStudentAssistant(models.Model):
    """Tracks a student assistant after their application is approved."""

    SEMESTER_CHOICES = [
        ('1st', '1st Semester'),
        ('2nd', '2nd Semester'),
        ('summer', 'Summer'),
    ]

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('expired', 'Expired'),
    ]

    # ── Link to the original application ──
    new_application = models.OneToOneField(
        NewApplication, null=True, blank=True, on_delete=models.CASCADE,
        related_name='active_sa',
    )
    renewal_application = models.OneToOneField(
        RenewalApplication, null=True, blank=True, on_delete=models.CASCADE,
        related_name='active_sa',
    )

    # ── Student identity (denormalized for quick access) ──
    student_id = models.CharField(max_length=8)
    full_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, default='')
    course = models.CharField(max_length=100, blank=True, default='')

    # ── Assignment details ──
    assigned_office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='active_assistants',
    )
    semester = models.CharField(max_length=10, choices=SEMESTER_CHOICES)
    academic_year = models.CharField(
        max_length=20, blank=True, default='',
        help_text='e.g. 2024-2025',
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # ── Attendance summary (cached for dashboard) ──
    total_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    required_hours = models.PositiveIntegerField(default=200, help_text='Total hours required for the semester.')

    # ── Status ──
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Active Student Assistant'
        verbose_name_plural = 'Active Student Assistants'

    def __str__(self):
        return f"{self.full_name} ({self.student_id}) — {self.get_status_display()}"

    @property
    def hours_percentage(self):
        if self.required_hours == 0:
            return 100
        return min(round(float(self.total_hours) / self.required_hours * 100, 1), 100)

    @property
    def application(self):
        """Return whichever application this SA record is linked to."""
        return self.new_application or self.renewal_application


class AttendanceRecord(models.Model):
    """Daily attendance log for an active student assistant."""

    STATUS_CHOICES = [
        ('present', 'Present'),
        ('late', 'Late'),
        ('absent', 'Absent'),
        ('excused', 'Excused'),
    ]

    student_assistant = models.ForeignKey(
        ActiveStudentAssistant, on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    date = models.DateField()
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='present')
    remarks = models.TextField(blank=True, default='')
    logged_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='attendance_logs',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-time_in']
        unique_together = ['student_assistant', 'date']
        verbose_name = 'Attendance Record'
        verbose_name_plural = 'Attendance Records'

    def __str__(self):
        return f"{self.student_assistant.full_name} — {self.date} ({self.get_status_display()})"

    @property
    def hours_worked(self):
        """Calculate hours worked from time_in and time_out."""
        if self.time_in and self.time_out:
            from datetime import datetime, timedelta
            dt_in = datetime.combine(self.date, self.time_in)
            dt_out = datetime.combine(self.date, self.time_out)
            if dt_out < dt_in:  # overnight
                dt_out += timedelta(days=1)
            diff = (dt_out - dt_in).total_seconds() / 3600
            return round(diff, 2)
        return 0


class PerformanceEvaluation(models.Model):
    """
    End-of-term or periodic performance evaluation for an active SA.
    Each criterion is scored 1–5.
    """

    PERIOD_CHOICES = [
        ('midterm', 'Midterm'),
        ('final', 'Final / End-of-Term'),
    ]

    student_assistant = models.ForeignKey(
        ActiveStudentAssistant, on_delete=models.CASCADE,
        related_name='evaluations',
    )
    evaluation_period = models.CharField(max_length=10, choices=PERIOD_CHOICES)

    # ── Rating criteria (1-5 scale) ──
    work_quality = models.PositiveSmallIntegerField(help_text='1 = Poor, 5 = Excellent')
    punctuality = models.PositiveSmallIntegerField(help_text='1 = Poor, 5 = Excellent')
    initiative = models.PositiveSmallIntegerField(help_text='1 = Poor, 5 = Excellent')
    cooperation = models.PositiveSmallIntegerField(help_text='1 = Poor, 5 = Excellent')
    communication = models.PositiveSmallIntegerField(help_text='1 = Poor, 5 = Excellent')

    overall_rating = models.DecimalField(max_digits=3, decimal_places=2, blank=True, null=True)
    remarks = models.TextField(blank=True, default='')

    evaluated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='evaluations_given',
    )
    evaluated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-evaluated_at']
        unique_together = ['student_assistant', 'evaluation_period']
        verbose_name = 'Performance Evaluation'
        verbose_name_plural = 'Performance Evaluations'

    def __str__(self):
        return f"{self.student_assistant.full_name} — {self.get_evaluation_period_display()}"

    def save(self, *args, **kwargs):
        # Auto-calculate overall rating as the average of all criteria
        scores = [self.work_quality, self.punctuality, self.initiative,
                  self.cooperation, self.communication]
        self.overall_rating = round(sum(scores) / len(scores), 2)
        super().save(*args, **kwargs)
