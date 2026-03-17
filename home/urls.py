from django.urls import path
from . import views

app_name = 'home'

urlpatterns = [
    path('', views.home, name='home'),
    path('offices/', views.available_offices, name='available_offices'),
    path('apply/new/', views.apply_new, name='apply_new'),
    path('apply/renew/', views.apply_renew, name='apply_renew'),
    path('apply/check-student/', views.check_student_id, name='check_student_id'),
    path('apply/camera-photo/', views.process_camera_photo, name='process_camera_photo'),
    path('apply/validate-document/', views.validate_document, name='validate_document'),

    # ---- Student Auth & Dashboard ----
    path('student/login/', views.student_login, name='student_login'),
    path('verify-email/<str:uidb64>/<str:token>/', views.verify_email, name='verify_email'),
    path('student/dashboard/', views.student_dashboard, name='student_dashboard'),
    path('student/clock-in/<int:pk>/', views.student_clock_in, name='student_clock_in'),
    path('student/clock-out/<int:pk>/', views.student_clock_out, name='student_clock_out'),
    path('student/duty-schedule/<int:pk>/', views.student_save_duty_schedule, name='student_save_duty_schedule'),

    path('staff/login/', views.staff_login, name='staff_login'),
    path('director/login/', views.director_login, name='director_login'),
    path('staff/', views.staff_dashboard, name='staff_dashboard'),
    path('staff/applications/<int:pk>/review/', views.staff_review_application, name='staff_review_application'),
    path('staff/applications/<int:pk>/status/', views.staff_update_application_status, name='staff_update_application_status'),
    path('staff/applications/<int:pk>/return-document/', views.staff_return_document, name='staff_return_document'),
    path('director/', views.director_dashboard, name='director_dashboard'),
    path('director/applications/<int:pk>/review/', views.director_review_application, name='director_review_application'),
    path('director/applications/<int:pk>/status/', views.director_update_application_status, name='director_update_application_status'),
    path('director/applications/<int:pk>/return-document/', views.director_return_document, name='director_return_document'),

    # ---- Staff CRUD: Reminders ----
    path('staff/reminders/add/', views.staff_add_reminder, name='staff_add_reminder'),
    path('staff/reminders/<int:pk>/edit/', views.staff_edit_reminder, name='staff_edit_reminder'),
    path('staff/reminders/<int:pk>/delete/', views.staff_delete_reminder, name='staff_delete_reminder'),

    # ---- Staff CRUD: Upcoming Dates ----
    path('staff/dates/add/', views.staff_add_date, name='staff_add_date'),
    path('staff/dates/<int:pk>/edit/', views.staff_edit_date, name='staff_edit_date'),
    path('staff/dates/<int:pk>/delete/', views.staff_delete_date, name='staff_delete_date'),

    # ---- Staff CRUD: Announcements ----
    path('staff/announcements/add/', views.staff_add_announcement, name='staff_add_announcement'),
    path('staff/announcements/<int:pk>/edit/', views.staff_edit_announcement, name='staff_edit_announcement'),
    path('staff/announcements/<int:pk>/delete/', views.staff_delete_announcement, name='staff_delete_announcement'),

    # ---- Staff CRUD: Offices ----
    path('staff/offices/add/', views.staff_add_office, name='staff_add_office'),
    path('staff/offices/<int:pk>/edit/', views.staff_edit_office, name='staff_edit_office'),
    path('staff/offices/<int:pk>/delete/', views.staff_delete_office, name='staff_delete_office'),
    path('staff/offices/<int:pk>/json/', views.staff_get_office_json, name='staff_get_office_json'),

    # ---- Director: Move Office Marker ----
    path('director/offices/<int:pk>/move/', views.director_move_office, name='director_move_office'),

    # ---- Staff: Active SA Management ----
    path('staff/sa/', views.staff_active_sa_list, name='staff_active_sa_list'),
    path('staff/sa/<int:pk>/', views.staff_sa_detail, name='staff_sa_detail'),
    path('staff/sa/<int:pk>/attendance/', views.staff_log_attendance, name='staff_log_attendance'),
    path('staff/sa/<int:pk>/attendance/<int:att_pk>/delete/', views.staff_delete_attendance, name='staff_delete_attendance'),
    path('staff/sa/<int:pk>/status/', views.staff_update_sa_status, name='staff_update_sa_status'),

    # ---- Director: Active SA Management ----
    path('director/sa/', views.director_sa_list, name='director_sa_list'),
    path('director/sa/<int:pk>/', views.director_sa_detail, name='director_sa_detail'),
    path('director/sa/<int:pk>/attendance/', views.director_log_attendance, name='director_log_attendance'),
    path('director/sa/<int:pk>/evaluate/', views.director_evaluate_sa, name='director_evaluate_sa'),
    path('director/sa/<int:pk>/status/', views.director_update_sa_status, name='director_update_sa_status'),
    path('sa/<int:pk>/certificate/', views.sa_completion_certificate, name='sa_completion_certificate'),
    path('director/reports/', views.director_department_reports, name='director_department_reports'),
    path('director/reports/pdf/', views.director_department_reports_pdf, name='director_department_reports_pdf'),
    path('director/reports/email/', views.director_department_reports_email, name='director_department_reports_email'),

    # ---- Student: Schedule & Document Resubmission ----
    path('resubmit-schedule/<str:app_type>/<int:pk>/', views.resubmit_schedule, name='resubmit_schedule'),
    path('resubmit-documents/<str:app_type>/<int:pk>/', views.resubmit_documents, name='resubmit_documents'),

    # ---- Staff: Notes & Schedule Verification ----
    path('staff/applications/<int:pk>/add-note/', views.staff_add_note, name='staff_add_note'),
    path('staff/applications/<int:pk>/verify-schedule/', views.staff_verify_schedule, name='staff_verify_schedule'),

    # ---- Director: Notes ----
    path('director/applications/<int:pk>/add-note/', views.director_add_note, name='director_add_note'),

    # ---- Staff: No-Duty Days ----
    path('staff/no-duty/add/', views.staff_add_no_duty_day, name='staff_add_no_duty_day'),
    path('staff/no-duty/<int:pk>/delete/', views.staff_delete_no_duty_day, name='staff_delete_no_duty_day'),

    # ---- CSV Exports ----
    path('staff/export/applications/', views.staff_export_applications_csv, name='staff_export_applications_csv'),
    path('staff/export/active-sa/', views.staff_export_active_sa_csv, name='staff_export_active_sa_csv'),
    path('staff/export/attendance/', views.staff_export_attendance_csv, name='staff_export_attendance_csv'),
    path('director/export/evaluations/', views.director_export_evaluations_csv, name='director_export_evaluations_csv'),
]
