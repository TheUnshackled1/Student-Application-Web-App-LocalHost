from django.contrib import admin
from .models import (
    StudentProfile, Document, ApplicationStep,
    UpcomingDate, Reminder, Announcement, NewApplication, RenewalApplication, Office,
    ActiveStudentAssistant, AttendanceRecord, PerformanceEvaluation,
    ApplicationNote, NoDutyDay,
)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'student_id', 'user', 'email_verified', 'created_at')
    search_fields = ('full_name', 'student_id')
    list_filter = ('email_verified',)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('name', 'student', 'status', 'uploaded_at')
    list_filter = ('status',)


@admin.register(ApplicationStep)
class ApplicationStepAdmin(admin.ModelAdmin):
    list_display = ('student', 'step_number', 'title', 'status')
    list_filter = ('status',)


@admin.register(UpcomingDate)
class UpcomingDateAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'is_active')


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ('message', 'student', 'is_active', 'created_at')


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'published_at', 'is_active')
    list_filter = ('is_active',)


@admin.register(NewApplication)
class NewApplicationAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'first_name', 'last_name', 'course', 'status', 'submitted_at')
    list_filter = ('status', 'gender', 'year_level', 'semester')
    search_fields = ('first_name', 'last_name', 'student_id', 'email')


@admin.register(RenewalApplication)
class RenewalApplicationAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'full_name', 'course', 'status', 'submitted_at')
    list_filter = ('status', 'year_level', 'semester')
    search_fields = ('full_name', 'student_id', 'email')


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ('name', 'building', 'room', 'head', 'total_slots', 'is_active')
    list_filter = ('is_active', 'building')
    search_fields = ('name', 'building', 'head')


class AttendanceInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    fields = ('date', 'time_in', 'time_out', 'status', 'hours_worked', 'logged_by')
    readonly_fields = ('hours_worked',)


class EvaluationInline(admin.TabularInline):
    model = PerformanceEvaluation
    extra = 0
    fields = ('evaluation_period', 'work_quality', 'punctuality', 'initiative',
              'cooperation', 'communication', 'overall_rating', 'evaluated_by')
    readonly_fields = ('overall_rating',)


@admin.register(ActiveStudentAssistant)
class ActiveStudentAssistantAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'full_name', 'assigned_office', 'semester',
                    'total_hours', 'required_hours', 'status', 'created_at')
    list_filter = ('status', 'semester', 'assigned_office')
    search_fields = ('full_name', 'student_id', 'email')
    inlines = [AttendanceInline, EvaluationInline]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('student_assistant', 'date', 'time_in', 'time_out', 'status', 'logged_by')
    list_filter = ('status', 'date')
    search_fields = ('student_assistant__full_name', 'student_assistant__student_id')


@admin.register(PerformanceEvaluation)
class PerformanceEvaluationAdmin(admin.ModelAdmin):
    list_display = ('student_assistant', 'evaluation_period', 'overall_rating',
                    'evaluated_by', 'evaluated_at')
    list_filter = ('evaluation_period',)


@admin.register(ApplicationNote)
class ApplicationNoteAdmin(admin.ModelAdmin):
    list_display = ('note_type', 'author', 'created_at')
    list_filter = ('note_type',)


@admin.register(NoDutyDay)
class NoDutyDayAdmin(admin.ModelAdmin):
    list_display = ('date', 'reason', 'office', 'created_by', 'created_at')
    list_filter = ('office', 'date')
    search_fields = ('reason',)
    search_fields = ('student_assistant__full_name', 'student_assistant__student_id')


@admin.register(ApplicationNote)
class ApplicationNoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'note_type', 'author', 'new_application', 'renewal_application', 'created_at')
    list_filter = ('note_type', 'created_at')
    search_fields = ('content',)
    raw_id_fields = ('new_application', 'renewal_application', 'author')
