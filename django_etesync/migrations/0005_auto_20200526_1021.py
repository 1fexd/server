# Generated by Django 3.0.3 on 2020-05-26 10:21

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('django_etesync', '0004_collectioninvitation_version'),
    ]

    operations = [
        migrations.RenameField(
            model_name='userinfo',
            old_name='pubkey',
            new_name='loginPubkey',
        ),
    ]
