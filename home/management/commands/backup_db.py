import os
import shutil
from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create an encrypted backup of the database and media files.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default=os.path.join(settings.BASE_DIR, 'backups'),
            help='Directory to store backups (default: <project>/backups/)',
        )
        parser.add_argument(
            '--no-encrypt',
            action='store_true',
            help='Skip encryption (not recommended for production)',
        )
        parser.add_argument(
            '--no-media',
            action='store_true',
            help='Skip backing up media files',
        )

    def handle(self, *args, **options):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = options['output_dir']
        backup_dir = os.path.join(output_dir, f'backup_{timestamp}')
        os.makedirs(backup_dir, exist_ok=True)

        encrypt = not options['no_encrypt']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Creating {"encrypted " if encrypt else ""}backup → {backup_dir}'
        ))

        # ── 1. Back up SQLite database file ──
        db_path = str(settings.DATABASES['default']['NAME'])
        if not os.path.exists(db_path):
            self.stderr.write(self.style.ERROR(f'Database not found: {db_path}'))
            return

        if encrypt:
            from home.encryption import encrypt_file
            dest = os.path.join(backup_dir, 'db.sqlite3.enc')
            encrypt_file(db_path, dest)
            self.stdout.write(self.style.SUCCESS(f'  Database (encrypted) → {dest}'))
        else:
            dest = os.path.join(backup_dir, 'db.sqlite3')
            shutil.copy2(db_path, dest)
            self.stdout.write(self.style.SUCCESS(f'  Database → {dest}'))

        # ── 2. JSON data dump ──
        json_tmp = os.path.join(backup_dir, 'data_dump.json')
        with open(json_tmp, 'w', encoding='utf-8') as f:
            call_command(
                'dumpdata',
                '--natural-foreign',
                '--natural-primary',
                '--exclude=contenttypes',
                '--exclude=auth.permission',
                '--indent=2',
                stdout=f,
            )

        if encrypt:
            from home.encryption import encrypt_file
            enc_json = json_tmp + '.enc'
            encrypt_file(json_tmp, enc_json)
            os.remove(json_tmp)
            self.stdout.write(self.style.SUCCESS(f'  JSON dump (encrypted) → {enc_json}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'  JSON dump → {json_tmp}'))

        # ── 3. Media files ──
        if not options['no_media']:
            media_root = str(settings.MEDIA_ROOT)
            if os.path.exists(media_root):
                media_dest = os.path.join(backup_dir, 'media')
                shutil.copytree(media_root, media_dest)
                self.stdout.write(self.style.SUCCESS(f'  Media files → {media_dest}'))
            else:
                self.stdout.write(self.style.WARNING('  No media directory found, skipping.'))

        self.stdout.write(self.style.SUCCESS(f'\n  Backup complete → {backup_dir}'))
