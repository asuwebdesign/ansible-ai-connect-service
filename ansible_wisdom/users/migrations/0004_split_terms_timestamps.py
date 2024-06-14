# Generated by Django 4.2.1 on 2023-07-13 19:36

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_user_date_terms_accepted"),
    ]

    operations = [
        migrations.RenameField(
            model_name="user",
            old_name="date_terms_accepted",
            new_name="community_terms_accepted",
        ),
        migrations.AddField(
            model_name="user",
            name="commercial_terms_accepted",
            field=models.DateTimeField(default=None, null=True),
        ),
    ]
