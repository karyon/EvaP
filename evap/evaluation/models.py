from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import models
from django.db.models import Count
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _
from django.template import Context, Template, TemplateSyntaxError, TemplateEncodingError
from django_fsm.db.fields import FSMField, transition
import django.dispatch

# see evaluation.meta for the use of Translate in this file
from evap.evaluation.meta import LocalizeModelBase, Translate

import datetime
import random

# for converting state into student_state
STUDENT_STATES_NAMES = {
    'new': 'upcoming',
    'prepared': 'upcoming',
    'lecturerApproved': 'upcoming',
    'approved': 'upcoming',
    'inEvaluation': 'inEvaluation',
    'evaluated': 'evaluationFinished',
    'reviewed': 'evaluationFinished',
    'published': 'published'
}


class Semester(models.Model):
    """Represents a semester, e.g. the winter term of 2011/2012."""

    __metaclass__ = LocalizeModelBase

    name_de = models.CharField(max_length=1024, unique=True, verbose_name=_(u"name (german)"))
    name_en = models.CharField(max_length=1024, unique=True, verbose_name=_(u"name (english)"))

    name = Translate

    created_at = models.DateField(verbose_name=_(u"created at"), auto_now_add=True)

    class Meta:
        ordering = ('-created_at', 'name_de')
        verbose_name = _(u"semester")
        verbose_name_plural = _(u"semesters")

    def __unicode__(self):
        return self.name

    @property
    def can_fsr_delete(self):
        for course in self.course_set.all():
            if not course.can_fsr_delete():
                return False
        return True

    @classmethod
    def get_all_with_published_courses(cls):
        return cls.objects.filter(course__state="published").distinct()


class Questionnaire(models.Model):
    """A named collection of questions."""

    __metaclass__ = LocalizeModelBase

    name_de = models.CharField(max_length=1024, unique=True, verbose_name=_(u"name (german)"))
    name_en = models.CharField(max_length=1024, unique=True, verbose_name=_(u"name (english)"))
    name = Translate

    description_de = models.TextField(verbose_name=_(u"description (german)"), blank=True, null=True)
    description_en = models.TextField(verbose_name=_(u"description (english)"), blank=True, null=True)
    description = Translate

    public_name_de = models.CharField(max_length=1024, verbose_name=_(u"display name (german)"))
    public_name_en = models.CharField(max_length=1024, verbose_name=_(u"display name (english)"))
    public_name = Translate

    teaser_de = models.TextField(verbose_name=_(u"teaser (german)"), blank=True, null=True)
    teaser_en = models.TextField(verbose_name=_(u"teaser (english)"), blank=True, null=True)
    teaser = Translate

    index = models.IntegerField(verbose_name=_(u"ordering index"))

    is_for_contributors = models.BooleanField(verbose_name=_(u"is for contributors"), default=False)
    obsolete = models.BooleanField(verbose_name=_(u"obsolete"), default=False)

    class Meta:
        ordering = ('obsolete', 'index', 'name_de')
        verbose_name = _(u"questionnaire")
        verbose_name_plural = _(u"questionnaires")

    def __unicode__(self):
        return self.name

    @property
    def can_fsr_delete(self):
        return not self.contributions.exists()


class Course(models.Model):
    """Models a single course, e.g. the Math 101 course of 2002."""

    __metaclass__ = LocalizeModelBase

    state = FSMField(default='new', protected=True)

    semester = models.ForeignKey(Semester, verbose_name=_(u"semester"))

    name_de = models.CharField(max_length=1024, verbose_name=_(u"name (german)"))
    name_en = models.CharField(max_length=1024, verbose_name=_(u"name (english)"))
    name = Translate

    # type of course: lecture, seminar, project
    kind = models.CharField(max_length=1024, verbose_name=_(u"type"))

    # bachelor, master, d-school course
    degree = models.CharField(max_length=1024, verbose_name=_(u"degree"))

    # students that are allowed to vote
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name=_(u"participants"), blank=True)
    participant_count = models.IntegerField(verbose_name=_(u"participant count"), blank=True, null=True, default=None)

    # students that already voted
    voters = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name=_(u"voters"), blank=True, related_name='+')
    voter_count = models.IntegerField(verbose_name=_(u"voter count"), blank=True, null=True, default=None)

    # when the evaluation takes place
    vote_start_date = models.DateField(null=True, verbose_name=_(u"first date to vote"))
    vote_end_date = models.DateField(null=True, verbose_name=_(u"last date to vote"))

    # who last modified this course, shell be noted
    last_modified_time = models.DateTimeField(auto_now=True)
    last_modified_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True)

    course_evaluated = django.dispatch.Signal(providing_args=['request', 'semester'])

    class Meta:
        ordering = ('semester', 'degree', 'name_de')
        unique_together = (
            ('semester', 'degree', 'name_de'),
            ('semester', 'degree', 'name_en'),
        )
        verbose_name = _(u"course")
        verbose_name_plural = _(u"courses")

    def __unicode__(self):
        return self.name

    def clean(self):
        if self.vote_start_date and self.vote_end_date:
            if self.vote_start_date >= self.vote_end_date:
                raise ValidationError(_(u"The vote start date must be before the vote end date."))

    def save(self, *args, **kw):
        super(Course, self).save(*args, **kw)

        # make sure there is a general contribution
        if not self.general_contribution:
            self.contributions.create(contributor=None)

    def is_fully_checked(self):
        """Shortcut for finding out whether all text answers to this course have been checked"""
        return not self.open_textanswer_set.exists()

    def can_user_vote(self, user):
        """Returns whether the user is allowed to vote on this course."""
        return (self.state == "inEvaluation"
            and datetime.date.today() <= self.vote_end_date
            and user in self.participants.all()
            and user not in self.voters.all())

    def can_user_see_results(self, user):
        if user.is_staff:
            return True
        if self.state == 'published':
            return self.can_publish_grades() or self.is_user_contributor_or_delegate(user)
        return False

    def can_fsr_edit(self):
        return self.state in ['new', 'prepared', 'lecturerApproved', 'approved', 'inEvaluation']

    def can_fsr_delete(self):
        return self.can_fsr_edit() and not self.voters.exists()

    def can_fsr_review(self):
        return self.state in ['inEvaluation', 'evaluated'] and not self.is_fully_checked()

    def can_fsr_approve(self):
        return self.state in ['new', 'prepared', 'lecturerApproved']

    def can_publish_grades(self):
        return self.num_voters >= settings.MIN_ANSWER_COUNT and float(self.num_voters) / self.num_participants >= settings.MIN_ANSWER_PERCENTAGE

    @transition(field=state, source=['new', 'lecturerApproved'], target='prepared')
    def ready_for_contributors(self):
        pass

    @transition(field=state, source='prepared', target='lecturerApproved')
    def contributor_approve(self):
        pass

    @transition(field=state, source=['new', 'prepared', 'lecturerApproved'], target='approved')
    def fsr_approve(self):
        pass

    @transition(field=state, source='prepared', target='new')
    def revert_to_new(self):
        pass

    @transition(field=state, source='approved', target='inEvaluation')
    def evaluation_begin(self):
        pass

    @transition(field=state, source='inEvaluation', target='evaluated')
    def evaluation_end(self):
        pass

    @transition(field=state, source='evaluated', target='reviewed', conditions=[is_fully_checked])
    def review_finished(self):
        pass

    @transition(field=state, source='reviewed', target='published')
    def publish(self):
        pass

    @transition(field=state, source='published', target='reviewed')
    def revoke(self):
        pass

    @property
    def student_state(self):
        return STUDENT_STATES_NAMES[self.state]

    @property
    def general_contribution(self):
        try:
            return self.contributions.get(contributor=None)
        except Contribution.DoesNotExist:
            return None

    @property
    def num_participants(self):
        if self.participant_count:
            return self.participant_count
        return self.participants.count()

    @property
    def num_voters(self):
        if self.voter_count:
            return self.voter_count
        return self.voters.count()

    @property
    def due_participants(self):
        return self.participants.exclude(pk__in=self.voters.all())

    @property
    def responsible_contributor(self):
        return self.contributions.get(responsible=True).contributor

    @property
    def responsible_contributors_name(self):
        return self.responsible_contributor.userprofile.full_name

    @property
    def responsible_contributors_username(self):
        return self.responsible_contributor.username

    def has_enough_questionnaires(self):
        return self.general_contribution and all(self.contributions.aggregate(Count('questionnaires')).values())

    def is_user_editor_or_delegate(self, user):
        if self.contributions.filter(can_edit=True, contributor=user).exists():
            return True
        else:
            represented_userprofiles = user.represented_users.all()
            represented_users = [profile.user for profile in represented_userprofiles]
            if self.contributions.filter(can_edit=True, contributor__in=represented_users).exists():
                return True

        return False

    def is_user_responsible_or_delegate(self, user):
        if self.contributions.filter(responsible=True, contributor=user).exists():
            return True
        else:
            represented_userprofiles = user.represented_users.all()
            represented_users = [profile.user for profile in represented_userprofiles]
            if self.contributions.filter(responsible=True, contributor__in=represented_users).exists():
                return True

        return False

    def is_user_contributor(self, user):
        return self.contributions.filter(contributor=user).exists()

    def is_user_contributor_or_delegate(self, user):
        if self.is_user_contributor(user):
            return True
        else:
            represented_userprofiles = user.represented_users.all()
            represented_users = [profile.user for profile in represented_userprofiles]
            if self.contributions.filter(contributor__in=represented_users).exists():
                return True

        return False

    def is_user_editor(self, user):
        return self.contributions.filter(contributor=user, can_edit=True).exists()

    def warnings(self):
        result = []
        if self.state == 'new' and not self.has_enough_questionnaires():
            result.append(_(u"Not enough questionnaires assigned"))
        if self.state in ['inEvaluation', 'evaluated', 'reviewed'] and not self.can_publish_grades():
            result.append(_(u"Not enough participants to publish results"))
        return result

    @property
    def textanswer_set(self):
        """Pseudo relationship to all text answers for this course"""
        return TextAnswer.objects.filter(contribution__in=self.contributions.all())

    @property
    def open_textanswer_set(self):
        """Pseudo relationship to all text answers for this course"""
        return TextAnswer.objects.filter(contribution__in=self.contributions.all(), checked=False)

    @property
    def checked_textanswer_set(self):
        """Pseudo relationship to all text answers for this course"""
        return TextAnswer.objects.filter(contribution__in=self.contributions.all(), checked=True)

    @property
    def likertanswer_set(self):
        """Pseudo relationship to all Likert answers for this course"""
        return LikertAnswer.objects.filter(contribution__in=self.contributions.all())

    @property
    def gradeanswer_set(self):
        """Pseudo relationship to all grade answers for this course"""
        return GradeAnswer.objects.filter(contribution__in=self.contributions.all())

    def was_evaluated(self, request):
        self.course_evaluated.send(sender=self.__class__, request=request, semester=self.semester)


class Contribution(models.Model):
    """A contributor who is assigned to a course and his questionnaires."""

    course = models.ForeignKey(Course, verbose_name=_(u"course"), related_name='contributions')
    contributor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_(u"contributor"), blank=True, null=True, related_name='contributions')
    questionnaires = models.ManyToManyField(Questionnaire, verbose_name=_(u"questionnaires"), blank=True, related_name="contributions")
    responsible = models.BooleanField(verbose_name=_(u"responsible"), default=False)
    can_edit = models.BooleanField(verbose_name=_(u"can edit"), default=False)

    order = models.IntegerField(verbose_name=_("contribution order"), default=0)

    class Meta:
        unique_together = (
            ('course', 'contributor'),
        )
        ordering = ['order', ]

    def clean(self):
        # responsible contributors can always edit
        if self.responsible:
            self.can_edit = True

    @property
    def is_general(self):
        return self.contributor == None


class Question(models.Model):
    """A question including a type."""

    __metaclass__ = LocalizeModelBase

    QUESTION_KINDS = (
        (u"T", _(u"Text Question")),
        (u"L", _(u"Likert Question")),
        (u"G", _(u"Grade Question")),
    )

    questionnaire = models.ForeignKey(Questionnaire)
    text_de = models.TextField(verbose_name=_(u"question text (german)"))
    text_en = models.TextField(verbose_name=_(u"question text (english)"))
    kind = models.CharField(max_length=1, choices=QUESTION_KINDS,
                            verbose_name=_(u"kind of question"))

    text = Translate

    class Meta:
        order_with_respect_to = 'questionnaire'
        verbose_name = _(u"question")
        verbose_name_plural = _(u"questions")

    @property
    def answer_class(self):
        if self.kind == u"T":
            return TextAnswer
        elif self.kind == u"L":
            return LikertAnswer
        elif self.kind == u"G":
            return GradeAnswer
        else:
            raise Exception("Unknown answer kind: %r" % self.kind)

    def is_likert_question(self):
        return self.answer_class == LikertAnswer

    def is_text_question(self):
        return self.answer_class == TextAnswer

    def is_grade_question(self):
        return self.answer_class == GradeAnswer


class Answer(models.Model):
    """An abstract answer to a question. For anonymity purposes, the answering
    user ist not stored in the object. Concrete subclasses are `LikertAnswer`,
    `TextAnswer` and `GradeAnswer`."""

    question = models.ForeignKey(Question)
    contribution = models.ForeignKey(Contribution)

    class Meta:
        abstract = True
        verbose_name = _(u"answer")
        verbose_name_plural = _(u"answers")


class LikertAnswer(Answer):
    """A Likert-scale answer to a question with `1` being *strongly agree* and `5`
    being *strongly disagree*."""

    answer = models.IntegerField(verbose_name=_(u"answer"))

    class Meta:
        verbose_name = _(u"Likert answer")
        verbose_name_plural = _(u"Likert answers")


class GradeAnswer(Answer):
    """A grade answer to a question with `1` being best and `5` being worst."""

    answer = models.IntegerField(verbose_name=_(u"answer"))

    class Meta:
        verbose_name = _(u"grade answer")
        verbose_name_plural = _(u"grade answers")


class TextAnswer(Answer):
    """A free-form text answer to a question (usually a comment about a course
    or a contributor)."""

    elements_per_page = 5

    reviewed_answer = models.TextField(verbose_name=_(u"reviewed answer"), blank=True, null=True)
    original_answer = models.TextField(verbose_name=_(u"original answer"), blank=True)

    checked = models.BooleanField(verbose_name=_(u"answer checked"), default=False)
    hidden = models.BooleanField(verbose_name=_(u"hide answer"), default=False)

    class Meta:
        verbose_name = _(u"text answer")
        verbose_name_plural = _(u"text answers")

    def _answer_get(self):
        return self.reviewed_answer or self.original_answer

    def _answer_set(self, value):
        self.original_answer = value
        self.reviewed_answer = None

    answer = property(_answer_get, _answer_set)


class FaqSection(models.Model):
    """Section in the frequently asked questions"""

    __metaclass__ = LocalizeModelBase

    order = models.IntegerField(verbose_name=_("section order"))

    title_de = models.TextField(verbose_name=_(u"section title (german)"))
    title_en = models.TextField(verbose_name=_(u"section title (english)"))
    title = Translate

    class Meta:
        ordering = ['order', ]
        verbose_name = _(u"section")
        verbose_name_plural = _(u"sections")


class FaqQuestion(models.Model):
    """Question and answer in the frequently asked questions"""

    __metaclass__ = LocalizeModelBase

    section = models.ForeignKey(FaqSection, related_name="questions")

    order = models.IntegerField(verbose_name=_("question order"))

    question_de = models.TextField(verbose_name=_("question (german)"))
    question_en = models.TextField(verbose_name=_("question (english)"))
    question = Translate

    answer_de = models.TextField(verbose_name=_("answer (german)"))
    answer_en = models.TextField(verbose_name=_("answer (english)"))
    answer = Translate

    class Meta:
        ordering = ['order', ]
        verbose_name = _(u"question")
        verbose_name_plural = _(u"questions")


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL)

    # extending first_name and last_name from the user
    title = models.CharField(verbose_name=_(u"Title"), max_length=1024, blank=True, null=True)

    # picture of the user
    picture = models.ImageField(verbose_name=_(u"Picture"), upload_to="pictures", blank=True, null=True)

    is_external = models.BooleanField(verbose_name=_(u"Is external"), default=False)

    # delegates of the user, which can also manage their courses
    delegates = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name=_(u"Delegates"), related_name="represented_users", blank=True)

    # users to which all emails should be sent in cc without giving them delegate rights
    cc_users = models.ManyToManyField(settings.AUTH_USER_MODEL, verbose_name=_(u"CC Users"), related_name="cc_users", blank=True)

    # key for url based login of this user
    MAX_LOGIN_KEY = 2**31-1

    login_key = models.IntegerField(verbose_name=_(u"Login Key"), blank=True, null=True)
    login_key_valid_until = models.DateField(verbose_name=_(u"Login Key Validity"), null=True)

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')

    def __unicode__(self):
        return unicode(self.user)

    @property
    def full_name(self):
        if self.user.last_name:
            name = self.user.last_name
            if self.user.first_name:
                name = self.user.first_name + " " + name
            if self.title:
                name = self.title + " " + name
            return name
        else:
            return self.user.username

    @property
    def can_fsr_delete(self):
        return not self.is_contributor

    @property
    def enrolled_in_courses(self):
        return self.user.course_set.exists()

    @property
    def is_contributor(self):
        return self.user.contributions.exists()

    @property
    def is_editor(self):
        return self.user.contributions.filter(can_edit=True).exists()

    @property
    def is_responsible(self):
        # in the user list, self.user.contributions is prefetched, therefore use it directly and don't filter it
        return any(contribution.responsible for contribution in self.user.contributions.all())

    @property
    def is_delegate(self):
        return self.user.represented_users.exists()

    @property
    def is_editor_or_delegate(self):
        return self.is_editor or self.is_delegate

    @classmethod
    def email_needs_login_key(cls, email):
        from evap.evaluation.tools import is_external_email
        return is_external_email(email)

    @property
    def needs_login_key(self):
        return UserProfile.email_needs_login_key(self.user.email)

    @classmethod
    def get_for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def generate_login_key(self):
        while True:
            key = random.randrange(0, UserProfile.MAX_LOGIN_KEY)
            if not UserProfile.objects.filter(login_key=key).exists():
                # key not yet used
                self.login_key = key
                break

        self.refresh_login_key()

    def refresh_login_key(self):
        self.login_key_valid_until = datetime.date.today() + datetime.timedelta(settings.LOGIN_KEY_VALIDITY)

    @staticmethod
    @receiver(post_save, sender=settings.AUTH_USER_MODEL)
    def create_user_profile(sender, instance, created, raw, **kwargs):
        """Creates a UserProfile object whenever a User is created."""
        if created and not raw:
            UserProfile.objects.create(user=instance)


def validate_template(value):
    """Field validator which ensures that the value can be compiled into a
    Django Template."""
    try:
        Template(value)
    except (TemplateSyntaxError, TemplateEncodingError) as e:
        raise ValidationError(str(e))


class EmailTemplate(models.Model):
    name = models.CharField(max_length=1024, unique=True, verbose_name=_("Name"))

    subject = models.CharField(max_length=1024, verbose_name=_(u"Subject"), validators=[validate_template])
    body = models.TextField(verbose_name=_("Body"), validators=[validate_template])

    @classmethod
    def get_review_template(cls):
        return cls.objects.get(name="Lecturer Review Notice")

    @classmethod
    def get_reminder_template(cls):
        return cls.objects.get(name="Student Reminder")

    @classmethod
    def get_publish_template(cls):
        return cls.objects.get(name="Publishing Notice")

    @classmethod
    def get_login_key_template(cls):
        return cls.objects.get(name="Login Key Created")

    @classmethod
    def recipient_list_for_course(cls, course, recipient_groups):
        recipients = []

        if "responsible" in recipient_groups:
            recipients += [course.responsible_contributor]

        if "contributors" in recipient_groups:
            recipients += [c.contributor for c in course.contributions.exclude(contributor=None)]
        elif "editors" in recipient_groups:
            recipients += [c.contributor for c in course.contributions.exclude(contributor=None).filter(can_edit=True)]

        if "all_participants" in recipient_groups:
            recipients += course.participants.all()
        elif "due_participants" in recipient_groups:
            recipients += course.due_participants

        return recipients

    @classmethod
    def render_string(cls, text, dictionary):
        return Template(text).render(Context(dictionary, autoescape=False))

    def send_to_users_in_courses(self, courses, recipient_groups):
        user_course_map = {}
        for course in courses:
            responsible = UserProfile.get_for_user(course.responsible_contributor)
            for user in self.recipient_list_for_course(course, recipient_groups):
                if user.email and user not in responsible.cc_users.all() and user not in responsible.delegates.all():
                    user_course_map.setdefault(user, []).append(course)

        for user, courses in user_course_map.iteritems():
            cc_users = []
            if ("responsible" in recipient_groups or "editors" in recipient_groups) and any(course.is_user_editor(user) for course in courses):
                cc_users += UserProfile.get_for_user(user).delegates.all()
            cc_users += UserProfile.get_for_user(user).cc_users.all()
            cc_addresses = [p.email for p in cc_users if p.email]

            mail = EmailMessage(
                subject = self.render_string(self.subject, {'user': user, 'courses': courses}),
                body = self.render_string(self.body, {'user': user, 'courses': courses}),
                to = [user.email],
                cc = cc_addresses,
                bcc = [a[1] for a in settings.MANAGERS],
                headers = {'Reply-To': settings.REPLY_TO_EMAIL})
            mail.send(False)

    def send_to_user(self, user):
        if not user.email:
            return

        mail = EmailMessage(
            subject = self.render_string(self.subject, {'user': user}),
            body = self.render_string(self.body, {'user': user}),
            to = [user.email],
            bcc = [a[1] for a in settings.MANAGERS],
            headers = {'Reply-To': settings.REPLY_TO_EMAIL})
        mail.send(False)
