from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):

    if isinstance(dictionary, dict):
        return dictionary.get(key, [])
    return []


@register.filter
def contains(value_list, item):
    """Check if item is in list. Returns True/False.

    Usage: {% if my_list|contains:item %}
    """
    if isinstance(value_list, (list, tuple)):
        return item in value_list
    return False


DOC_FIELD_LABELS = {
    'application_form': 'Application Form',
    'id_picture': '2x2 ID Picture',
    'barangay_clearance': 'Barangay Clearance',
    'parents_itr': "Parent's ITR / Certificate of Indigency",
    'enrolment_form': 'Certificate of Enrolment',
    'schedule_classes': 'Schedule of Classes',
    'proof_insurance': 'Proof of Insurance',
    'grades_last_sem': 'Grades Last Semester',
    'official_time': 'Official Time',
    'recommendation_letter': 'Recommendation Letter',
    'evaluation_form': 'Evaluation Form',
    'id_picture_renewal': 'Updated 2x2 ID Picture',
}


@register.filter
def doc_label(field_name):
    """Convert a document field name to a human-readable label."""
    return DOC_FIELD_LABELS.get(field_name, field_name.replace('_', ' ').title())


@register.filter
def mask_sid(student_id, own_id=''):
    """Mask a student ID for privacy: 202***** unless it matches own_id."""
    sid = str(student_id)
    if own_id and sid == str(own_id):
        return sid
    if len(sid) > 3:
        return sid[:3] + '*' * (len(sid) - 3)
    return sid
