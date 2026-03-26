from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from home.models import (
    ActiveStudentAssistant, AttendanceRecord, DutyReminder, NoDutyDay,
)
from home.email_utils import (
    send_shift_reminder_email, send_absent_notification_email,
    send_consecutive_absence_alert, send_late_threshold_alert,
)
import logging

logger = logging.getLogger(__name__)


def _parse_slot_times(slot_label):
    from datetime import datetime
    try:
        parts = slot_label.split(' - ')
        start = datetime.strptime(parts[0].strip(), '%I:%M %p').time()
        end = datetime.strptime(parts[1].strip(), '%I:%M %p').time()
        return start, end
    except (ValueError, IndexError):
        return None, None


def _merge_consecutive_slots(raw_slots):
    from datetime import datetime, timedelta as td
    if not raw_slots:
        return []
    parsed = []
    for slot in raw_slots:
        s, e = _parse_slot_times(slot)
        if s and e:
            parsed.append((s, e))
    if not parsed:
        return []
    parsed.sort()
    merged = [list(parsed[0])]
    for s, e in parsed[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    result = []
    for s, e in merged:
        s_str = datetime.combine(datetime.today(), s).strftime('%I:%M %p').lstrip('0')
        e_str = datetime.combine(datetime.today(), e).strftime('%I:%M %p').lstrip('0')
        result.append(f"{s_str} - {e_str}")
    return result


class Command(BaseCommand):
    help = 'Send 5-minute shift reminders and absent notifications via email.'

    def handle(self, *args, **options):
        ph_now = timezone.localtime()
        today = ph_now.date()
        now_time = ph_now.time()
        day_name = ph_now.strftime('%A')

        # Skip weekends
        if today.weekday() >= 5:
            self.stdout.write('Weekend — no notifications to send.')
            return

        # Check for global no-duty day
        if NoDutyDay.objects.filter(date=today, office__isnull=True).exists():
            self.stdout.write('Global no-duty day — skipping.')
            return

        no_duty_office_ids = set(
            NoDutyDay.objects.filter(date=today).values_list('office_id', flat=True)
        )
        active_sas = ActiveStudentAssistant.objects.filter(
            status='active',
            duty_schedule__isnull=False,
        ).select_related('assigned_office')
        reminders_sent = 0
        absent_sent = 0
        for sa in active_sas:
            if sa.assigned_office_id in no_duty_office_ids:
                continue
            if sa.start_date and today < sa.start_date:
                continue
            if sa.end_date and today > sa.end_date:
                continue
            raw_slots = (sa.duty_schedule or {}).get(day_name, [])
            merged_shifts = _merge_consecutive_slots(raw_slots)
            for shift_label in merged_shifts:
                slot_start, slot_end = _parse_slot_times(shift_label)
                if not slot_start:
                    continue
                from datetime import datetime, timedelta as td
                shift_start_dt = datetime.combine(today, slot_start)
                now_dt = datetime.combine(today, now_time)
                minutes_until = (shift_start_dt - now_dt).total_seconds() / 60
                if 0 < minutes_until <= 5:
                    _, created = DutyReminder.objects.get_or_create(
                        student_assistant=sa,
                        date=today,
                        shift=shift_label,
                        reminder_type='upcoming',
                    )
                    if created:
                        if send_shift_reminder_email(sa, shift_label):
                            reminders_sent += 1
                            self.stdout.write(
                                f'  Reminder sent to {sa.full_name} for {shift_label}'
                            )
                if now_time > slot_end:
                    has_record = AttendanceRecord.objects.filter(
                        student_assistant=sa, date=today, shift=shift_label,
                    ).exclude(status='absent').exists()
                    if not has_record:
                        record, rec_created = AttendanceRecord.objects.get_or_create(
                            student_assistant=sa,
                            date=today,
                            shift=shift_label,
                            defaults={'status': 'absent'},
                        )
                        # Send absent notification
                        _, notif_created = DutyReminder.objects.get_or_create(
                            student_assistant=sa,
                            date=today,
                            shift=shift_label,
                            reminder_type='absent',
                        )
                        if notif_created:
                            if send_absent_notification_email(sa, today, shift_label):
                                absent_sent += 1
                                self.stdout.write(
                                    f'  Absent notice sent to {sa.full_name} for {shift_label}'
                                )

        # ── Consecutive absence & late threshold alerts (once per day) ──
        from home.views import _check_consecutive_absences, _check_late_threshold
        from home.views import CONSECUTIVE_ABSENCE_THRESHOLD, LATE_MONTHLY_THRESHOLD

        consec_alerts = 0
        late_alerts = 0

        for sa in active_sas:
            if sa.assigned_office_id in no_duty_office_ids:
                continue
            if sa.start_date and today < sa.start_date:
                continue
            if sa.end_date and today > sa.end_date:
                continue

            # Consecutive absences
            consec_count, consec_dates = _check_consecutive_absences(sa)
            if consec_count >= CONSECUTIVE_ABSENCE_THRESHOLD:
                key = f'consec_{consec_count}'
                _, created = DutyReminder.objects.get_or_create(
                    student_assistant=sa,
                    date=today,
                    shift=key,
                    reminder_type='absent',
                )
                if created:
                    if send_consecutive_absence_alert(sa, consec_count, consec_dates):
                        consec_alerts += 1
                        self.stdout.write(f'  Consecutive absence alert sent to {sa.full_name} ({consec_count} days)')

            # Late threshold
            late_count, late_month = _check_late_threshold(sa)
            if late_count >= LATE_MONTHLY_THRESHOLD:
                key = f'late_{today.year}_{today.month}'
                _, created = DutyReminder.objects.get_or_create(
                    student_assistant=sa,
                    date=today,
                    shift=key,
                    reminder_type='upcoming',
                )
                if created:
                    if send_late_threshold_alert(sa, late_count, late_month):
                        late_alerts += 1
                        self.stdout.write(f'  Late threshold alert sent to {sa.full_name} ({late_count} in {late_month})')

        self.stdout.write(self.style.SUCCESS(
            f'Done — {reminders_sent} reminder(s), {absent_sent} absent notice(s), '
            f'{consec_alerts} consecutive-absence alert(s), {late_alerts} late-threshold alert(s) sent.'
        ))
