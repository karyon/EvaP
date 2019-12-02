# Generated by Django 2.1.8 on 2019-05-27 17:59

from django.db import migrations, models
import uuid


def fill_rating_answer_counter_uuids(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    RatingAnswerCounter = apps.get_model('evaluation', 'RatingAnswerCounter')
    for obj in RatingAnswerCounter.objects.using(db_alias).all():
        obj.uuid = uuid.uuid4()
        obj.save()


class Migration(migrations.Migration):

    dependencies = [
        ('evaluation', '0104_userprofile_is_proxy_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='ratinganswercounter',
            name='uuid',
            field=models.UUIDField(null=True),
        ),
        migrations.RunPython(
            fill_rating_answer_counter_uuids,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='ratinganswercounter',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=False, serialize=False),
        ),
        # see evaluation/migrations/0062_replace_textanswer_id_with_uuid.py - same problem
        migrations.RemoveField(
            'RatingAnswerCounter',
            'id',
        ),
        migrations.RenameField(
            model_name='ratinganswercounter',
            old_name='uuid',
            new_name='id'
        ),
        migrations.AlterField(
            model_name='ratinganswercounter',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
    ]