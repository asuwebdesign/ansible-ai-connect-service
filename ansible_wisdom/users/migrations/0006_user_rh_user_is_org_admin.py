# Generated by Django 4.2.3 on 2023-10-10 20:38

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_add_organization_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="rh_user_is_org_admin",
            field=models.BooleanField(default=None, null=True),
        ),
    ]
