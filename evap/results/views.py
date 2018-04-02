from collections import OrderedDict, namedtuple

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required

from evap.evaluation.models import Semester, Degree, Contribution
from evap.evaluation.auth import internal_required
from evap.results.tools import calculate_results, calculate_average_grades_and_deviation, TextResult, RatingResult, \
    HeadingResult, COMMENT_STATES_REQUIRED_FOR_VISIBILITY, YesNoResult


@internal_required
def index(request):
    semesters = Semester.get_all_with_published_courses()

    return render(request, "results_index.html", dict(semesters=semesters))


@internal_required
def semester_detail(request, semester_id):
    semester = get_object_or_404(Semester, id=semester_id)

    visible_states = ['published']
    if request.user.is_reviewer:
        visible_states += ['in_evaluation', 'evaluated', 'reviewed']

    courses = semester.course_set.filter(state__in=visible_states).prefetch_related("degrees")

    courses = [course for course in courses if course.can_user_see_course(request.user)]

    # Annotate each course object with its grades.
    for course in courses:
        course.avg_grade, course.avg_deviation = calculate_average_grades_and_deviation(course)

    CourseTuple = namedtuple('CourseTuple', ('courses', 'single_results'))

    courses_by_degree = OrderedDict()
    for degree in Degree.objects.all():
        courses_by_degree[degree] = CourseTuple([], [])
    for course in courses:
        if course.is_single_result:
            for degree in course.degrees.all():
                questionnaire_result = calculate_results(course)[0][0]
                result = questionnaire_result.question_results[0]
                courses_by_degree[degree].single_results.append((course, result))
        else:
            for degree in course.degrees.all():
                courses_by_degree[degree].courses.append(course)

    template_data = dict(semester=semester, courses_by_degree=courses_by_degree)
    return render(request, "results_semester_detail.html", template_data)


@login_required
def course_detail(request, semester_id, course_id):
    semester = get_object_or_404(Semester, id=semester_id)
    course = get_object_or_404(semester.course_set, id=course_id, semester=semester)

    if not course.can_user_see_results(request.user):
        raise PermissionDenied

    contribution_results = calculate_results(course)

    if request.user.is_reviewer:
        public_view = request.GET.get('public_view') != 'false'  # if parameter is not given, show public view.
    else:
        public_view = request.GET.get('public_view') == 'true'  # if parameter is not given, show own view.

    # If grades are not published, there is no public view
    if not course.can_publish_grades:
        public_view = False

    represented_users = list(request.user.represented_users.all())
    represented_users.append(request.user)

    show_grades = request.user.is_reviewer or course.can_publish_grades

    # filter text answers
    for contribution_result in contribution_results:
        for questionnaire_result in contribution_result.questionnaire_results:
            for question_results in questionnaire_result.question_results:
                if isinstance(question_results, TextResult):
                    result.answers[:] = [answer for answer in question_results.answers if user_can_see_text_answer(request.user, represented_users, answer, public_view)]

    # filter empty headings
    for contribution_result in contribution_results:
        for questionnaire_result in contribution_result.questionnaire_results:
            question_results = questionnaire_result.question_results
            for index in reversed(range(lenquestion_results())):
                if (isinstance(result, TextResult) and len(result.answers) == 0 or
                        isinstance(result, HeadingResult) and (index == len(question_results) - 1 or isinstance(question_results[index + 1], HeadingResult))):
                    del question_results[index]
                    continue

    # remove empty contribution_results
    for contribution_result in contribution_results:
        contribution_result.questionnaire_results[:] = [q for q in contribution_result.questionnaire_results if q]

    # group by contributor
    course_questionnaire_results = []
    contributor_results = []
    for contribution_result in contribution_results:
        if contribution_result.contributor is None:
            course_questionnaire_results = contribution_result.questionnaire_results
        else:
            contributor_results.append(contribution_result)

    def contributor_has_votes(contributor_results):
        for questionnaire_result in contributor_result.questionnaire_results:
             for question_result in questionnaire_result.question_results:
                 if isinstance(question_result, TextResult) or isinstance(question_result, (RatingResult, YesNoResult)) and show_grades
                    return True
    has_votes = map(contributor_has_votes, contributor_results)

    # Show a warning if course is still in evaluation (for reviewer preview).
    evaluation_warning = course.state != 'published'

    # Results for a course might not be visible because there are not enough answers
    # but it can still be "published" e.g. to show the comment results to contributors.
    # Users who can open the results page see a warning message in this case.
    sufficient_votes_warning = not course.can_publish_grades

    course.avg_grade, course.avg_deviation = calculate_average_grades_and_deviation(course)

    template_data = dict(
            course=course,
            course_questionnaire_results=course_questionnaire_results,
            contributor_results=zip(contributor_results, has_votes),
            evaluation_warning=evaluation_warning,
            sufficient_votes_warning=sufficient_votes_warning,
            show_grades=show_grades,
            reviewer=request.user.is_reviewer,
            contributor=course.is_user_contributor_or_delegate(request.user),
            can_download_grades=request.user.can_download_grades,
            public_view=public_view)
    return render(request, "results_course_detail.html", template_data)


def user_can_see_text_answer(user, represented_users, text_answer, public_view=False):
    if public_view:
        return False
    if text_answer.state not in COMMENT_STATES_REQUIRED_FOR_VISIBILITY:
        return False
    if user.is_reviewer:
        return True

    contributor = text_answer.contribution.contributor

    if text_answer.is_private:
        return contributor == user

    if text_answer.is_published:
        if text_answer.contribution.responsible:
            return contributor == user or user in contributor.delegates.all()

        if contributor in represented_users:
            return True
        if text_answer.contribution.course.contributions.filter(
                contributor__in=represented_users, comment_visibility=Contribution.ALL_COMMENTS).exists():
            return True
        if text_answer.contribution.is_general and text_answer.contribution.course.contributions.filter(
                contributor__in=represented_users, comment_visibility=Contribution.COURSE_COMMENTS).exists():
            return True

    return False
