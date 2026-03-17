from django.contrib import admin
from .models import (
    StudentProfile, Document, ApplicationStep,
    UpcomingDate, Reminder, Announcement, NewApplication, RenewalApplication, Office,
    ActiveStudentAssistant, AttendanceRecord, PerformanceEvaluation,
    ApplicationNote, NoDutyDay, DutyReminder,
)


# ══════════════════════════════════════════════════
#  Admin Site Branding
# ══════════════════════════════════════════════════

admin.site.site_header = "SWA Application System"
admin.site.site_title = "SWA Admin"
admin.site.index_title = "Administration Dashboard"


# ══════════════════════════════════════════════════
#  Student Profile
# ══════════════════════════════════════════════════

@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'student_id', 'user', 'email_verified', 'created_at')
    search_fields = ('full_name', 'student_id', 'user__username')
    list_filter = ('email_verified', 'created_at')
    list_per_page = 25
    readonly_fields = ('created_at',)


# ══════════════════════════════════════════════════
#  Documents & Application Steps
# ══════════════════════════════════════════════════

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('name', 'student', 'status', 'uploaded_at')
    list_filter = ('status',)
    search_fields = ('name', 'student__full_name')
    list_per_page = 25


@admin.register(ApplicationStep)
class ApplicationStepAdmin(admin.ModelAdmin):
    list_display = ('student', 'step_number', 'title', 'status')
    list_filter = ('status',)
    search_fields = ('student__full_name', 'title')
    list_per_page = 25


# ══════════════════════════════════════════════════
#  Content Management (Dates, Reminders, Announcements)
# ══════════════════════════════════════════════════

@admin.register(UpcomingDate)
class UpcomingDateAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'expires_at', 'is_active')
    list_filter = ('is_active', 'date')
    search_fields = ('title',)
    list_per_page = 25


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ('message_preview', 'priority', 'student', 'is_active', 'expires_at', 'created_at')
    list_filter = ('priority', 'is_active', 'created_at')
    search_fields = ('message',)
    list_per_page = 25

    @admin.display(description='Message')
    def message_preview(self, obj):
        return obj.message[:80] + '...' if len(obj.message) > 80 else obj.message


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'published_at', 'expires_at', 'is_active')
    list_filter = ('is_active', 'published_at')
    search_fields = ('title', 'summary')
    list_per_page = 25
    date_hierarchy = 'published_at'


# ══════════════════════════════════════════════════
#  New Applications
# ══════════════════════════════════════════════════

class ApplicationNoteInline(admin.TabularInline):
    model = ApplicationNote
    fk_name = 'new_application'
    extra = 0
    fields = ('note_type', 'content', 'author', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(NewApplication)
class NewApplicationAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'first_name', 'last_name', 'course', 'year_level',
                    'preferred_office', 'status', 'submitted_at')
    list_filter = ('status', 'gender', 'year_level', 'semester', 'preferred_office')
    search_fields = ('first_name', 'last_name', 'student_id', 'email')
    date_hierarchy = 'submitted_at'
    list_per_page = 25
    readonly_fields = ('submitted_at',)
    inlines = [ApplicationNoteInline]

    fieldsets = (
        ('Personal Information', {
            'fields': ('user', 'first_name', 'middle_initial', 'last_name', 'extension_name',
                       'date_of_birth', 'gender', 'contact_number', 'email', 'address')
        }),
        ('Academic Information', {
            'fields': ('student_id', 'course', 'year_level', 'semester', 'gpa')
        }),
        ('Office Preference & Schedule', {
            'fields': ('preferred_office', 'availability_schedule', 'schedule_verified',
                       'schedule_mismatch_note')
        }),
        ('Documents', {
            'classes': ('collapse',),
            'fields': ('application_form', 'id_picture', 'barangay_clearance',
                       'parents_itr', 'enrolment_form', 'schedule_classes',
                       'proof_insurance', 'grades_last_sem', 'official_time')
        }),
        ('Workflow', {
            'fields': ('status', 'interview_date', 'assigned_office', 'start_date', 'submitted_at')
        }),
        ('Notes & Remarks', {
            'classes': ('collapse',),
            'fields': ('staff_notes', 'director_notes', 'requested_documents_note', 'returned_documents')
        }),
    )


# ══════════════════════════════════════════════════
#  Renewal Applications
# ══════════════════════════════════════════════════

class RenewalNoteInline(admin.TabularInline):
    model = ApplicationNote
    fk_name = 'renewal_application'
    extra = 0
    fields = ('note_type', 'content', 'author', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(RenewalApplication)
class RenewalApplicationAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'full_name', 'course', 'previous_office',
                    'preferred_office', 'status', 'submitted_at')
    list_filter = ('status', 'year_level', 'semester', 'preferred_office')
    search_fields = ('full_name', 'student_id', 'email')
    date_hierarchy = 'submitted_at'
    list_per_page = 25
    readonly_fields = ('submitted_at',)
    inlines = [RenewalNoteInline]

    fieldsets = (
        ('Student Information', {
            'fields': ('user', 'student_id', 'full_name', 'email', 'contact_number', 'address')
        }),
        ('Academic Information', {
            'fields': ('course', 'year_level', 'semester', 'gpa')
        }),
        ('Assignment & Schedule', {
            'fields': ('previous_office', 'preferred_office', 'hours_rendered', 'supervisor_name',
                       'availability_schedule', 'schedule_verified', 'schedule_mismatch_note')
        }),
        ('Documents', {
            'classes': ('collapse',),
            'fields': ('id_picture', 'enrolment_form', 'schedule_classes',
                       'grades_last_sem', 'official_time', 'recommendation_letter', 'evaluation_form')
        }),
        ('Workflow', {
            'fields': ('status', 'interview_date', 'assigned_office', 'start_date', 'submitted_at')
        }),
        ('Notes & Remarks', {
            'classes': ('collapse',),
            'fields': ('staff_notes', 'director_notes', 'requested_documents_note', 'returned_documents')
        }),
    )


# ══════════════════════════════════════════════════
#  Offices
# ══════════════════════════════════════════════════

@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ('name', 'building', 'room', 'head', 'total_slots', 'is_active')
    list_filter = ('is_active', 'building')
    search_fields = ('name', 'building', 'head')
    list_per_page = 25

    fieldsets = (
        (None, {
            'fields': ('name', 'building', 'room', 'head', 'hours', 'description', 'icon')
        }),
        ('Capacity & Status', {
            'fields': ('total_slots', 'is_active')
        }),
        ('Map Coordinates', {
            'classes': ('collapse',),
            'fields': ('latitude', 'longitude')
        }),
    )


# ══════════════════════════════════════════════════
#  Active Student Assistants
# ══════════════════════════════════════════════════

class AttendanceInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    fields = ('date', 'shift', 'time_in', 'time_out', 'status', 'remarks', 'logged_by')
    readonly_fields = ('created_at',)


class EvaluationInline(admin.TabularInline):
    model = PerformanceEvaluation
    extra = 0
    fields = ('evaluation_period', 'work_quality', 'punctuality', 'initiative',
              'cooperation', 'communication', 'overall_rating', 'recommendation_status', 'evaluated_by')
    readonly_fields = ('overall_rating',)


@admin.register(ActiveStudentAssistant)
class ActiveStudentAssistantAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'full_name', 'assigned_office', 'semester',
                    'academic_year', 'total_hours', 'required_hours', 'status', 'start_date', 'end_date')
    list_filter = ('status', 'semester', 'assigned_office', 'academic_year')
    search_fields = ('full_name', 'student_id', 'email')
    list_per_page = 25
    readonly_fields = ('created_at',)
    inlines = [AttendanceInline, EvaluationInline]

    fieldsets = (
        ('Student Info', {
            'fields': ('student_id', 'full_name', 'email', 'course')
        }),
        ('Application Link', {
            'classes': ('collapse',),
            'fields': ('new_application', 'renewal_application')
        }),
        ('Assignment', {
            'fields': ('assigned_office', 'semester', 'academic_year', 'start_date', 'end_date')
        }),
        ('Hours & Status', {
            'fields': ('total_hours', 'required_hours', 'status', 'duty_schedule')
        }),
    )


# ══════════════════════════════════════════════════
#  Attendance Records
# ══════════════════════════════════════════════════

@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('student_assistant', 'date', 'shift', 'time_in', 'time_out', 'status', 'logged_by')
    list_filter = ('status', 'date', 'student_assistant__assigned_office')
    search_fields = ('student_assistant__full_name', 'student_assistant__student_id')
    date_hierarchy = 'date'
    list_per_page = 25


# ══════════════════════════════════════════════════
#  Performance Evaluations
# ══════════════════════════════════════════════════

@admin.register(PerformanceEvaluation)
class PerformanceEvaluationAdmin(admin.ModelAdmin):
    list_display = ('student_assistant', 'evaluation_period', 'overall_rating',
                    'recommendation_status', 'evaluated_by', 'evaluated_at')
    list_filter = ('evaluation_period', 'recommendation_status')
    search_fields = ('student_assistant__full_name', 'student_assistant__student_id')
    list_per_page = 25
    readonly_fields = ('overall_rating', 'evaluated_at')


# ══════════════════════════════════════════════════
#  No-Duty Days
# ══════════════════════════════════════════════════

@admin.register(NoDutyDay)
class NoDutyDayAdmin(admin.ModelAdmin):
    list_display = ('date', 'reason', 'office', 'created_by', 'created_at')
    list_filter = ('office', 'date')
    search_fields = ('reason',)
    date_hierarchy = 'date'
    list_per_page = 25


# ══════════════════════════════════════════════════
#  Application Notes (Audit Log)
# ══════════════════════════════════════════════════

@admin.register(ApplicationNote)
class ApplicationNoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'note_type', 'author', 'new_application', 'renewal_application', 'created_at')
    list_filter = ('note_type', 'created_at')
    search_fields = ('content', 'author__username')
    raw_id_fields = ('new_application', 'renewal_application', 'author')
    date_hierarchy = 'created_at'
    list_per_page = 25


# ══════════════════════════════════════════════════
#  Duty Reminders
# ══════════════════════════════════════════════════

@admin.register(DutyReminder)
class DutyReminderAdmin(admin.ModelAdmin):
    list_display = ('student_assistant', 'date', 'shift', 'reminder_type', 'sent_at')
    list_filter = ('reminder_type', 'date')
    search_fields = ('student_assistant__full_name', 'student_assistant__student_id')
    date_hierarchy = 'date'
    list_per_page = 25
