# Generated by Django 4.2.16 on 2024-09-13 21:05

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0013_user_email_verified_user_family_name_user_given_name_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="commercial_terms_accepted",
        ),
        migrations.RemoveField(
            model_name="user",
            name="community_terms_accepted",
        ),
    ]
