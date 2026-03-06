"""
Email utility functions for the SWA Application System.
Sends confirmation and status-update emails to applicants.
"""
from django.core.mail import send_mail
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def _get_status_display(application, status_key):
    """Get human-readable status label from choices."""
    return dict(application.STATUS_CHOICES).get(status_key, status_key)


def send_application_confirmation(application, app_type='new'):
    """Send confirmation email after successful application submission."""
    if not application.email:
        logger.warning('No email for %s, skipping confirmation.', application.student_id)
        return False

    type_label = 'New Application' if app_type == 'new' else 'Renewal Application'
    subject = f'SWA Application Received — {application.student_id}'

    message = (
        f"Dear {_applicant_name(application)},\n\n"
        f"Thank you for submitting your {type_label} for the Student Assistant program.\n\n"
        f"Application Details:\n"
        f"  • Student ID : {application.student_id}\n"
        f"  • Type       : {type_label}\n"
        f"  • Status     : Pending\n\n"
        f"Your application is now under review.  You will receive email updates\n"
        f"whenever the status changes.  You can also track your application on\n"
        f"the website using your Student ID.\n\n"
        f"— SWA Application System"
    )

    return _send(subject, message, application.email)


def send_status_update_email(application, old_status, new_status, extra_message=''):
    """Send email whenever an application status changes."""
    if not application.email:
        return False

    new_label = _get_status_display(application, new_status)
    old_label = _get_status_display(application, old_status)
    subject = f'SWA Application Update — {new_label}'

    body_map = {
        'under_review':
            'Your application is now being reviewed by our staff.',
        'schedule_mismatch':
            'We found a mismatch between your availability schedule and your '
            'uploaded Schedule of Classes.  Please log in and re-submit your '
            'availability schedule.',
        'documents_requested':
            f'Additional documents are required for your application.\n\n'
            f'Details from staff:\n{extra_message}',
        'interview_scheduled':
            f'An interview has been scheduled for your application.\n{extra_message}',
        'interview_done':
            'Your interview has been completed.  We will notify you of the next steps.',
        'office_assigned':
            'You have been assigned to an office.  Check your application for details.',
        'approved':
            'Congratulations!  Your application has been APPROVED.\n'
            'Welcome to the Student Assistant program!',
        'rejected':
            f'We regret to inform you that your application has been rejected.\n{extra_message}',
    }

    status_msg = body_map.get(new_status, f'Your status has been updated to: {new_label}')

    message = (
        f"Dear {_applicant_name(application)},\n\n"
        f"Your SWA application status has been updated.\n\n"
        f"  • Student ID      : {application.student_id}\n"
        f"  • Previous Status  : {old_label}\n"
        f"  • New Status       : {new_label}\n\n"
        f"{status_msg}\n\n"
        f"Track your application on the website using your Student ID.\n\n"
        f"— SWA Application System"
    )

    return _send(subject, message, application.email)


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


def _send(subject, message, recipient):
    """Wrapper around Django send_mail with error handling."""
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
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

    # Build absolute link
    if request:
        base = request.build_absolute_uri('/')[:-1]
    else:
        base = 'http://localhost:8000'

    link = f"{base}/verify-email/{uid}/{token}/"
    subject = 'SWA Application — Verify Your Email'
    message = (
        f"Dear {user.first_name} {user.last_name},\n\n"
        f"Thank you for registering on the SWA Application System.\n\n"
        f"Please verify your email by clicking the link below:\n"
        f"  {link}\n\n"
        f"If you did not create this account, you can ignore this email.\n\n"
        f"— SWA Application System"
    )
    return _send(subject, message, user.email)
