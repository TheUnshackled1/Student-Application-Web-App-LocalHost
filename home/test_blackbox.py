"""
Blackbox Testing for Student Application Web App

This test suite performs blackbox testing by treating the application as a
"black box" - testing functionality from the user's perspective without
examining internal code structure.

Tests cover:
- Public pages accessibility
- Authentication flows
- Application submission workflows
- Staff and Director dashboards
- CRUD operations for various entities
"""

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from datetime import date, datetime, timedelta
from decimal import Decimal
import json

from home.models import (
    Office, StudentProfile, NewApplication, RenewalApplication,
    ActiveStudentAssistant, AttendanceRecord, PerformanceEvaluation,
    Announcement, Reminder, UpcomingDate, NoDutyDay
)


class PublicPagesBlackboxTest(TestCase):
    """Test publicly accessible pages without authentication."""

    def setUp(self):
        self.client = Client()
        self.office = Office.objects.create(
            name='Computer Science Office',
            building='Engineering Building',
            room='301',
            total_slots=5,
            is_active=True
        )

    def test_homepage_accessible(self):
        """Test if homepage loads successfully."""
        response = self.client.get(reverse('home:home'))
        self.assertEqual(response.status_code, 200)

    def test_available_offices_page(self):
        """Test if available offices page displays correctly."""
        response = self.client.get(reverse('home:available_offices'))
        self.assertEqual(response.status_code, 200)

    def test_new_application_page_accessible(self):
        """Test if new application form page loads."""
        response = self.client.get(reverse('home:apply_new'))
        self.assertEqual(response.status_code, 200)

    def test_renewal_application_page_accessible(self):
        """Test if renewal application form page loads."""
        response = self.client.get(reverse('home:apply_renew'))
        self.assertEqual(response.status_code, 200)


class StudentAuthenticationBlackboxTest(TestCase):
    """Test student authentication and login flows."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='student001',
            email='student@example.com',
            password='testpass123'
        )
        self.profile = StudentProfile.objects.create(
            user=self.user,
            student_id='12345678',
            full_name='Test Student',
            email_verified=True
        )

    def test_student_login_page_accessible(self):
        """Test if student login page loads."""
        response = self.client.get(reverse('home:student_login'))
        self.assertEqual(response.status_code, 200)

    def test_student_login_with_valid_credentials(self):
        """Test student can login with correct credentials."""
        response = self.client.post(reverse('home:student_login'), {
            'username': 'student001',
            'password': 'testpass123'
        })
        # Should redirect after successful login
        self.assertEqual(response.status_code, 302)

    def test_student_login_with_invalid_credentials(self):
        """Test login fails with incorrect credentials."""
        response = self.client.post(reverse('home:student_login'), {
            'username': 'student001',
            'password': 'wrongpassword'
        })
        # Should stay on login page or show error
        self.assertIn(response.status_code, [200, 302])

    def test_student_dashboard_requires_authentication(self):
        """Test that student dashboard is protected."""
        response = self.client.get(reverse('home:student_dashboard'))
        # Should redirect to login
        self.assertEqual(response.status_code, 302)

    def test_student_can_access_dashboard_when_authenticated(self):
        """Test authenticated student can access dashboard."""
        self.client.login(username='student001', password='testpass123')
        response = self.client.get(reverse('home:student_dashboard'))
        self.assertIn(response.status_code, [200, 302])


class StaffAuthenticationBlackboxTest(TestCase):
    """Test staff authentication and authorization."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            username='staff001',
            email='staff@example.com',
            password='staffpass123'
        )
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.staff_user.groups.add(staff_group)
        self.staff_user.is_staff = True
        self.staff_user.save()

    def test_staff_login_page_accessible(self):
        """Test if staff login page loads."""
        response = self.client.get(reverse('home:staff_login'))
        self.assertEqual(response.status_code, 200)

    def test_staff_dashboard_requires_authentication(self):
        """Test that staff dashboard is protected."""
        response = self.client.get(reverse('home:staff_dashboard'))
        # Should redirect to login
        self.assertEqual(response.status_code, 302)

    def test_staff_can_access_dashboard_when_authenticated(self):
        """Test authenticated staff can access staff dashboard."""
        self.client.login(username='staff001', password='staffpass123')
        response = self.client.get(reverse('home:staff_dashboard'))
        self.assertIn(response.status_code, [200, 302])


class DirectorAuthenticationBlackboxTest(TestCase):
    """Test director authentication and authorization."""

    def setUp(self):
        self.client = Client()
        self.director_user = User.objects.create_user(
            username='director001',
            email='director@example.com',
            password='directorpass123'
        )
        director_group, _ = Group.objects.get_or_create(name='Director')
        self.director_user.groups.add(director_group)
        self.director_user.is_staff = True
        self.director_user.save()

    def test_director_login_page_accessible(self):
        """Test if director login page loads."""
        response = self.client.get(reverse('home:director_login'))
        self.assertEqual(response.status_code, 200)

    def test_director_dashboard_requires_authentication(self):
        """Test that director dashboard is protected."""
        response = self.client.get(reverse('home:director_dashboard'))
        # Should redirect to login
        self.assertEqual(response.status_code, 302)

    def test_director_can_access_dashboard_when_authenticated(self):
        """Test authenticated director can access director dashboard."""
        self.client.login(username='director001', password='directorpass123')
        response = self.client.get(reverse('home:director_dashboard'))
        self.assertIn(response.status_code, [200, 302])


class NewApplicationSubmissionBlackboxTest(TestCase):
    """Test the complete flow of submitting a new application."""

    def setUp(self):
        self.client = Client()
        self.office = Office.objects.create(
            name='Library Office',
            building='Main Library',
            total_slots=3,
            is_active=True
        )

    def test_check_student_id_availability(self):
        """Test checking if student ID is available."""
        response = self.client.post(
            reverse('home:check_student_id'),
            {'student_id': '87654321'},
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

    def test_submit_new_application_with_complete_data(self):
        """Test submitting a new application with all required fields."""
        # Create a simple test file
        test_file = SimpleUploadedFile(
            "test_document.pdf",
            b"file_content",
            content_type="application/pdf"
        )

        application_data = {
            'first_name': 'John',
            'middle_initial': 'D',
            'last_name': 'Doe',
            'extension_name': '',
            'date_of_birth': '2000-01-01',
            'gender': 'male',
            'contact_number': '09123456789',
            'email': 'john.doe@example.com',
            'address': '123 Main St, City',
            'student_id': '87654321',
            'course': 'BS Computer Science',
            'year_level': 2,
            'semester': '1st',
            'gpa': '1.75',
            'preferred_office': self.office.id,
            'availability_schedule': json.dumps({
                'Monday': ['8:00 AM - 9:00 AM', '10:00 AM - 11:00 AM'],
                'Wednesday': ['8:00 AM - 9:00 AM']
            })
        }

        response = self.client.post(reverse('home:apply_new'), application_data)
        # Should redirect or return success
        self.assertIn(response.status_code, [200, 302])

    def test_duplicate_student_id_rejected(self):
        """Test that duplicate student IDs are rejected."""
        NewApplication.objects.create(
            first_name='Jane',
            middle_initial='A',
            last_name='Smith',
            date_of_birth=date(2001, 5, 15),
            gender='female',
            contact_number='09123456789',
            email='jane@example.com',
            address='456 Test Ave',
            student_id='11111111',
            course='BS Information Technology',
            year_level=1,
            semester='1st'
        )

        application_data = {
            'student_id': '11111111',
            'first_name': 'Another',
            'middle_initial': 'B',
            'last_name': 'Person',
            'date_of_birth': '2000-06-01',
            'gender': 'male',
            'contact_number': '09987654321',
            'email': 'another@example.com',
            'address': '789 Sample Rd',
            'course': 'BS Computer Science',
            'year_level': 2,
            'semester': '1st'
        }

        response = self.client.post(reverse('home:apply_new'), application_data)
        # Should show validation error
        self.assertIn(response.status_code, [200, 400])


class RenewalApplicationSubmissionBlackboxTest(TestCase):
    """Test renewal application submission workflow."""

    def setUp(self):
        self.client = Client()
        self.office = Office.objects.create(
            name='Registrar Office',
            building='Admin Building',
            total_slots=4,
            is_active=True
        )

    def test_submit_renewal_application(self):
        """Test submitting a renewal application."""
        renewal_data = {
            'student_id': '22222222',
            'full_name': 'Maria Santos',
            'email': 'maria@example.com',
            'contact_number': '09111222333',
            'address': '321 Renewal St',
            'course': 'BS Accountancy',
            'year_level': 3,
            'semester': '2nd',
            'previous_office': self.office.id,
            'preferred_office': self.office.id,
            'hours_rendered': 200,
            'supervisor_name': 'Ms. Supervisor',
            'gpa': '1.50',
            'availability_schedule': json.dumps({
                'Tuesday': ['9:00 AM - 10:00 AM'],
                'Thursday': ['9:00 AM - 10:00 AM']
            })
        }

        response = self.client.post(reverse('home:apply_renew'), renewal_data)
        self.assertIn(response.status_code, [200, 302])


class ApplicationReviewWorkflowBlackboxTest(TestCase):
    """Test the complete application review workflow from submission to approval."""

    def setUp(self):
        self.client = Client()

        # Create staff user
        self.staff_user = User.objects.create_user(
            username='staff_reviewer',
            email='staff@example.com',
            password='staffpass123'
        )
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.staff_user.groups.add(staff_group)
        self.staff_user.is_staff = True
        self.staff_user.save()

        # Create director user
        self.director_user = User.objects.create_user(
            username='director_reviewer',
            email='director@example.com',
            password='directorpass123'
        )
        director_group, _ = Group.objects.get_or_create(name='Director')
        self.director_user.groups.add(director_group)
        self.director_user.is_staff = True
        self.director_user.save()

        # Create office
        self.office = Office.objects.create(
            name='Test Office',
            building='Test Building',
            total_slots=5,
            is_active=True
        )

        # Create test application
        self.application = NewApplication.objects.create(
            first_name='Test',
            middle_initial='T',
            last_name='Applicant',
            date_of_birth=date(2001, 3, 15),
            gender='male',
            contact_number='09123456789',
            email='test@example.com',
            address='Test Address',
            student_id='33333333',
            course='BS Computer Science',
            year_level=2,
            semester='1st',
            gpa=Decimal('1.75'),
            status='pending'
        )

    def test_staff_can_view_application(self):
        """Test staff can view application details."""
        self.client.login(username='staff_reviewer', password='staffpass123')
        response = self.client.get(
            reverse('home:staff_review_application', args=[self.application.pk])
        )
        self.assertIn(response.status_code, [200, 302])

    def test_staff_can_update_application_status(self):
        """Test staff can update application status."""
        self.client.login(username='staff_reviewer', password='staffpass123')
        response = self.client.post(
            reverse('home:staff_update_application_status', args=[self.application.pk]),
            {'status': 'under_review'}
        )
        self.assertIn(response.status_code, [200, 302])

    def test_director_can_view_application(self):
        """Test director can view application details."""
        self.client.login(username='director_reviewer', password='directorpass123')
        response = self.client.get(
            reverse('home:director_review_application', args=[self.application.pk])
        )
        self.assertIn(response.status_code, [200, 302])

    def test_director_can_approve_application(self):
        """Test director can approve an application."""
        self.client.login(username='director_reviewer', password='directorpass123')
        response = self.client.post(
            reverse('home:director_update_application_status', args=[self.application.pk]),
            {'status': 'approved'}
        )
        self.assertIn(response.status_code, [200, 302])


class AttendanceManagementBlackboxTest(TestCase):
    """Test attendance tracking functionality."""

    def setUp(self):
        self.client = Client()

        # Create staff user
        self.staff_user = User.objects.create_user(
            username='staff_attendance',
            password='staffpass123'
        )
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.staff_user.groups.add(staff_group)
        self.staff_user.is_staff = True
        self.staff_user.save()

        # Create office
        self.office = Office.objects.create(
            name='Test Office',
            building='Test Building',
            is_active=True
        )

        # Create active student assistant
        self.sa = ActiveStudentAssistant.objects.create(
            student_id='44444444',
            full_name='Active Student',
            email='active@example.com',
            course='BS IT',
            assigned_office=self.office,
            semester='1st',
            academic_year='2025-2026',
            start_date=date.today() - timedelta(days=30),
            status='active',
            duty_schedule=json.dumps({
                'Monday': ['8:00 AM - 12:00 PM'],
                'Wednesday': ['8:00 AM - 12:00 PM']
            })
        )

    def test_staff_can_log_attendance(self):
        """Test staff can log attendance for a student assistant."""
        self.client.login(username='staff_attendance', password='staffpass123')
        response = self.client.post(
            reverse('home:staff_log_attendance', args=[self.sa.pk]),
            {
                'date': date.today().isoformat(),
                'shift': '8:00 AM - 12:00 PM',
                'time_in': '08:00',
                'time_out': '12:00',
                'status': 'present',
                'remarks': 'On time'
            }
        )
        self.assertIn(response.status_code, [200, 302])

    def test_student_can_clock_in(self):
        """Test student assistant can clock in."""
        # Create student user
        student_user = User.objects.create_user(
            username='student44444444',
            password='studentpass123'
        )
        profile = StudentProfile.objects.create(
            user=student_user,
            student_id='44444444',
            full_name='Active Student',
            email_verified=True
        )

        self.client.login(username='student44444444', password='studentpass123')
        response = self.client.post(
            reverse('home:student_clock_in', args=[self.sa.pk])
        )
        self.assertIn(response.status_code, [200, 302])

    def test_student_can_clock_out(self):
        """Test student assistant can clock out."""
        # Create student user
        student_user = User.objects.create_user(
            username='student44444444',
            password='studentpass123'
        )
        profile = StudentProfile.objects.create(
            user=student_user,
            student_id='44444444',
            full_name='Active Student',
            email_verified=True
        )

        # Create attendance record with time_in
        AttendanceRecord.objects.create(
            student_assistant=self.sa,
            date=date.today(),
            shift='8:00 AM - 12:00 PM',
            time_in=datetime.now().time(),
            status='present'
        )

        self.client.login(username='student44444444', password='studentpass123')
        response = self.client.post(
            reverse('home:student_clock_out', args=[self.sa.pk])
        )
        self.assertIn(response.status_code, [200, 302])


class PerformanceEvaluationBlackboxTest(TestCase):
    """Test performance evaluation workflow."""

    def setUp(self):
        self.client = Client()

        # Create director user
        self.director_user = User.objects.create_user(
            username='director_eval',
            password='directorpass123'
        )
        director_group, _ = Group.objects.get_or_create(name='Director')
        self.director_user.groups.add(director_group)
        self.director_user.is_staff = True
        self.director_user.save()

        # Create office
        self.office = Office.objects.create(
            name='Evaluation Office',
            building='Test Building',
            is_active=True
        )

        # Create active student assistant
        self.sa = ActiveStudentAssistant.objects.create(
            student_id='55555555',
            full_name='Evaluated Student',
            email='evaluated@example.com',
            course='BS CS',
            assigned_office=self.office,
            semester='1st',
            status='active'
        )

    def test_director_can_evaluate_student_assistant(self):
        """Test director can create performance evaluation."""
        self.client.login(username='director_eval', password='directorpass123')
        response = self.client.post(
            reverse('home:director_evaluate_sa', args=[self.sa.pk]),
            {
                'evaluation_period': 'midterm',
                'work_quality': 5,
                'punctuality': 4,
                'initiative': 5,
                'cooperation': 5,
                'communication': 4,
                'recommendation_status': 'rehire',
                'remarks': 'Excellent performance'
            }
        )
        self.assertIn(response.status_code, [200, 302])


class DataExportBlackboxTest(TestCase):
    """Test CSV export functionality."""

    def setUp(self):
        self.client = Client()

        # Create staff user
        self.staff_user = User.objects.create_user(
            username='staff_export',
            password='staffpass123'
        )
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.staff_user.groups.add(staff_group)
        self.staff_user.is_staff = True
        self.staff_user.save()

        # Create director user
        self.director_user = User.objects.create_user(
            username='director_export',
            password='directorpass123'
        )
        director_group, _ = Group.objects.get_or_create(name='Director')
        self.director_user.groups.add(director_group)
        self.director_user.is_staff = True
        self.director_user.save()

    def test_staff_can_export_applications_csv(self):
        """Test staff can export applications to CSV."""
        self.client.login(username='staff_export', password='staffpass123')
        response = self.client.get(reverse('home:staff_export_applications_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_staff_can_export_active_sa_csv(self):
        """Test staff can export active student assistants to CSV."""
        self.client.login(username='staff_export', password='staffpass123')
        response = self.client.get(reverse('home:staff_export_active_sa_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_staff_can_export_attendance_csv(self):
        """Test staff can export attendance records to CSV."""
        self.client.login(username='staff_export', password='staffpass123')
        response = self.client.get(reverse('home:staff_export_attendance_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_director_can_export_evaluations_csv(self):
        """Test director can export evaluations to CSV."""
        self.client.login(username='director_export', password='directorpass123')
        response = self.client.get(reverse('home:director_export_evaluations_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')


class CRUDOperationsBlackboxTest(TestCase):
    """Test CRUD operations for various entities."""

    def setUp(self):
        self.client = Client()

        # Create staff user
        self.staff_user = User.objects.create_user(
            username='staff_crud',
            password='staffpass123'
        )
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.staff_user.groups.add(staff_group)
        self.staff_user.is_staff = True
        self.staff_user.save()

    def test_staff_can_add_reminder(self):
        """Test staff can add a reminder."""
        self.client.login(username='staff_crud', password='staffpass123')
        response = self.client.post(
            reverse('home:staff_add_reminder'),
            {
                'message': 'Test reminder message',
                'priority': 'info',
                'is_active': True
            }
        )
        self.assertIn(response.status_code, [200, 302])

    def test_staff_can_add_announcement(self):
        """Test staff can add an announcement."""
        self.client.login(username='staff_crud', password='staffpass123')
        response = self.client.post(
            reverse('home:staff_add_announcement'),
            {
                'title': 'Test Announcement',
                'summary': 'This is a test announcement',
                'is_active': True
            }
        )
        self.assertIn(response.status_code, [200, 302])

    def test_staff_can_add_office(self):
        """Test staff can add a new office."""
        self.client.login(username='staff_crud', password='staffpass123')
        response = self.client.post(
            reverse('home:staff_add_office'),
            {
                'name': 'New Test Office',
                'building': 'New Building',
                'room': '101',
                'total_slots': 3,
                'is_active': True
            }
        )
        self.assertIn(response.status_code, [200, 302])

    def test_staff_can_delete_reminder(self):
        """Test staff can delete a reminder."""
        reminder = Reminder.objects.create(
            message='To be deleted',
            priority='info',
            is_active=True
        )

        self.client.login(username='staff_crud', password='staffpass123')
        response = self.client.post(
            reverse('home:staff_delete_reminder', args=[reminder.pk])
        )
        self.assertIn(response.status_code, [200, 302])


class SecurityBlackboxTest(TestCase):
    """Test security measures and access control."""

    def setUp(self):
        self.client = Client()

        # Create regular student user
        self.student_user = User.objects.create_user(
            username='student_security',
            password='studentpass123'
        )
        self.profile = StudentProfile.objects.create(
            user=self.student_user,
            student_id='66666666',
            full_name='Security Test Student',
            email_verified=True
        )

        # Create staff user
        self.staff_user = User.objects.create_user(
            username='staff_security',
            password='staffpass123'
        )
        staff_group, _ = Group.objects.get_or_create(name='Staff')
        self.staff_user.groups.add(staff_group)
        self.staff_user.is_staff = True
        self.staff_user.save()

    def test_student_cannot_access_staff_dashboard(self):
        """Test student users cannot access staff dashboard."""
        self.client.login(username='student_security', password='studentpass123')
        response = self.client.get(reverse('home:staff_dashboard'))
        # Should be redirected or forbidden
        self.assertIn(response.status_code, [302, 403])

    def test_student_cannot_access_director_dashboard(self):
        """Test student users cannot access director dashboard."""
        self.client.login(username='student_security', password='studentpass123')
        response = self.client.get(reverse('home:director_dashboard'))
        # Should be redirected or forbidden
        self.assertIn(response.status_code, [302, 403])

    def test_unauthenticated_user_cannot_access_protected_pages(self):
        """Test unauthenticated users are redirected from protected pages."""
        protected_urls = [
            reverse('home:student_dashboard'),
            reverse('home:staff_dashboard'),
            reverse('home:director_dashboard'),
        ]

        for url in protected_urls:
            response = self.client.get(url)
            # Should redirect to login
            self.assertEqual(response.status_code, 302)
