

import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Restore database and media from a backup directory.'

    def add_arguments(self, parser):
        parser.add_argument(
            'backup_dir',
            help='Path to the backup directory (e.g. backups/backup_20260313_120000)',
        )
        parser.add_argument(
            '--no-media',
            action='store_true',
            help='Skip restoring media files',
        )

    def handle(self, *args, **options):
        backup_dir = options['backup_dir']
        if not os.path.isdir(backup_dir):
            self.stderr.write(self.style.ERROR(f'Backup directory not found: {backup_dir}'))
            return

        db_dest = str(settings.DATABASES['default']['NAME'])

        # ── 1. Restore database ──
        enc_db = os.path.join(backup_dir, 'db.sqlite3.enc')
        plain_db = os.path.join(backup_dir, 'db.sqlite3')

        if os.path.exists(enc_db):
            from home.encryption import decrypt_file
            self.stdout.write('Decrypting database backup...')
            decrypt_file(enc_db, db_dest)
            self.stdout.write(self.style.SUCCESS(f'  Database restored (decrypted) → {db_dest}'))
        elif os.path.exists(plain_db):
            shutil.copy2(plain_db, db_dest)
            self.stdout.write(self.style.SUCCESS(f'  Database restored → {db_dest}'))
        else:
            self.stderr.write(self.style.ERROR(
                'No database file found in backup (expected db.sqlite3.enc or db.sqlite3).'
            ))
            return

        # ── 2. Restore media ──
        if not options['no_media']:
            media_src = os.path.join(backup_dir, 'media')
            media_dest = str(settings.MEDIA_ROOT)
            if os.path.exists(media_src):
                if os.path.exists(media_dest):
                    shutil.rmtree(media_dest)
                shutil.copytree(media_src, media_dest)
                self.stdout.write(self.style.SUCCESS(f'  Media restored → {media_dest}'))
            else:
                self.stdout.write(self.style.WARNING('  No media folder in backup, skipping.'))

        self.stdout.write(self.style.SUCCESS('\n  Restore complete.'))
