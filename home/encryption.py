from cryptography.fernet import Fernet
from django.conf import settings


def get_fernet():
    """Return a Fernet instance using the configured encryption key."""
    key = getattr(settings, 'DATA_ENCRYPTION_KEY', None)
    if not key:
        raise ValueError(
            "DATA_ENCRYPTION_KEY is not set in settings.py. "
            "Generate one with:  python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_file(input_path, output_path):
    """Read *input_path*, encrypt its contents, write to *output_path*."""
    fernet = get_fernet()
    with open(input_path, 'rb') as f:
        plaintext = f.read()
    with open(output_path, 'wb') as f:
        f.write(fernet.encrypt(plaintext))


def decrypt_file(input_path, output_path):
    """Read *input_path*, decrypt its contents, write to *output_path*."""
    fernet = get_fernet()
    with open(input_path, 'rb') as f:
        ciphertext = f.read()
    with open(output_path, 'wb') as f:
        f.write(fernet.decrypt(ciphertext))
