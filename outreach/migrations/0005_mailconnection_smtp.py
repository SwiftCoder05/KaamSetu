from django.db import migrations, models


def gmail_smtp_to_gmail(apps, schema_editor):
    apps.get_model("outreach", "MailConnection").objects.filter(provider="gmail_smtp").update(provider="gmail")


class Migration(migrations.Migration):

    dependencies = [
        ("outreach", "0004_recipient_job_context"),
    ]

    operations = [
        # New columns default to Gmail over SSL/TLS, which is exactly what every existing connection already uses.
        migrations.AddField(model_name="mailconnection", name="smtp_host", field=models.CharField(default="smtp.gmail.com", max_length=253)),
        migrations.AddField(model_name="mailconnection", name="smtp_port", field=models.PositiveIntegerField(default=465)),
        migrations.AddField(
            model_name="mailconnection",
            name="security",
            field=models.CharField(choices=[("ssl", "SSL/TLS (port 465)"), ("starttls", "STARTTLS (port 587)")], default="ssl", max_length=8),
        ),
        migrations.AddField(
            model_name="mailconnection",
            name="smtp_username",
            field=models.CharField(blank=True, help_text="Blank means the email address.", max_length=254),
        ),
        migrations.AlterField(model_name="mailconnection", name="provider", field=models.CharField(default="gmail", max_length=20)),
        migrations.AlterField(
            model_name="mailconnection",
            name="secret_enc",
            field=models.TextField(help_text="Encrypted SMTP password. Never shown again after saving."),
        ),
        migrations.RunPython(gmail_smtp_to_gmail, migrations.RunPython.noop),
    ]
