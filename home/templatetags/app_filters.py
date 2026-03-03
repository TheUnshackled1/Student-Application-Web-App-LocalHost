from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Retrieve a value from a dict by key in templates.

    Usage: {{ mydict|get_item:key_var }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key, [])
    return []
