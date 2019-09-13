import csv
import datetime as dt
import json
import os
from glob import glob

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from django.views.generic.base import TemplateView, View
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from hspatial import PointTimeseries

from .forms import AgrifieldForm, IrrigationlogForm, ProfileForm
from .irma.main import (
    agripoint_in_raster,
    get_default_db_value,
    get_parameters,
    get_performance_chart,
    model_results,
)
from .models import Agrifield, IrrigationLog, Profile


class IrrigationPerformance(TemplateView):
    template_name = "aira/performance-chart.html"

    def get_context_data(self, **kwargs):
        context = super(IrrigationPerformance, self).get_context_data(**kwargs)
        # Load data paths
        f = Agrifield.objects.get(pk=self.kwargs["pk_a"])
        f.can_edit(self.request.user)
        f.chart = get_performance_chart(f)
        if f.chart:
            f.chart.sum_ifinal = sum(f.chart.chart_ifinal)
            f.chart.sum_applied_water = sum(f.chart.applied_water)
            f.chart.percentage_diff = _("Not Available")
            if f.chart.sum_ifinal != 0.0:
                f.chart.percentage_diff = round(
                    (
                        (f.chart.sum_applied_water - f.chart.sum_ifinal)
                        / f.chart.sum_ifinal
                    )
                    * 100
                    or 0.0
                )
        context["f"] = f
        return context


def performance_csv(request, pk):
    f = Agrifield.objects.get(pk=pk)
    response = HttpResponse(content_type="text/csv")
    response[
        "Content-Disposition"
    ] = 'attachment; filename="{}-performance.csv"'.format(f.id)
    f.can_edit(request.user)
    results = get_performance_chart(f)
    writer = csv.writer(response)
    writer.writerow(
        [
            "Date",
            "Estimated Irrigation Water Amount",
            "Applied Irrigation Water Amount",
            "Effective precipitation",
        ]
    )
    writer.writerow(["", "amount (mm)", "amount (mm)", "amount (mm)"])
    for row in zip(
        results.chart_dates,
        results.chart_ifinal,
        results.applied_water,
        results.chart_peff,
    ):
        writer.writerow(row)
    return response


class TryPageView(TemplateView):
    def get(self, request):
        user = authenticate(username="demo", password="demo")
        login(request, user)
        return redirect("home", user)


class ConversionTools(TemplateView):
    template_name = "aira/tools.html"


class IndexPageView(TemplateView):
    template_name = "aira/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filenames = sorted(
            glob(os.path.join(settings.AIRA_DATA_HISTORICAL, "daily_rain-*.tif"))
        )
        one_day = dt.timedelta(days=1)
        context["start_date"] = self._get_date_from_filename(filenames[0])
        context["end_date"] = self._get_date_from_filename(filenames[-1]) - one_day
        return context

    def _get_date_from_filename(self, filename):
        datestr = os.path.basename(filename).split(".")[0].partition("-")[2]
        y, m, d = (int(x) for x in datestr.split("-"))
        return dt.date(y, m, d)


class HomePageView(TemplateView):
    template_name = "aira/home/main.html"

    def get_context_data(self, **kwargs):
        context = super(HomePageView, self).get_context_data(**kwargs)
        # Load data paths
        url_username = kwargs.get("username")
        context["url_username"] = kwargs.get("username")
        if kwargs.get("username") is None:
            url_username = self.request.user
            context["url_username"] = self.request.user
        # User is url_slug <username>
        user = User.objects.get(username=url_username)

        # Fetch models.Profile(User)
        try:
            context["profile"] = Profile.objects.get(farmer=self.request.user)
        except Profile.DoesNotExist:
            context["profile"] = None
        # Fetch models.Agrifield(User)
        try:
            agrifields = Agrifield.objects.filter(owner=user).all()
            for f in agrifields:
                # Check if user is allowed or 404
                f.can_edit(self.request.user)
            # For Profile section
            # Select self.request.user user that set him supervisor
            if Profile.objects.filter(supervisor=self.request.user).exists():
                supervising_users = Profile.objects.filter(supervisor=self.request.user)
                context["supervising_users"] = supervising_users

            for f in agrifields:
                if not agripoint_in_raster(f):
                    f.outside_arta_raster = True
                f.results = model_results(f)

            context["agrifields"] = agrifields
            context["fields_count"] = len(agrifields)
        except Agrifield.DoesNotExist:
            context["agrifields"] = None
        return context


class AdvicePageView(TemplateView):
    template_name = "aira/advice.html"

    def get_context_data(self, **kwargs):
        context = super(AdvicePageView, self).get_context_data(**kwargs)
        # Load data paths
        f = Agrifield.objects.get(pk=self.kwargs["pk"])
        f.can_edit(self.request.user)

        # This is a voodoo fix that should go away when we go to Leaflet and
        # cleanup all this part.
        f.location.transform("EPSG:4326")

        context["f"] = f
        if not agripoint_in_raster(f):
            return context
        context["fpars"] = get_parameters(f)
        f.results = model_results(f)

        # This is a voodoo fix that should go away when we go to Leaflet and
        # cleanup all this part.
        f.location.transform("EPSG:4326")

        return context


# Profile Create/Update
class CreateProfile(CreateView):
    model = Profile
    form_class = ProfileForm
    success_url = "/home"

    def get_form(self, form_class=None):
        form = super(CreateProfile, self).get_form(form_class)
        if self.request.user in form.fields["supervisor"].queryset:
            form.fields["supervisor"].queryset = form.fields[
                "supervisor"
            ].queryset.exclude(pk=self.request.user.id)
        return form

    def form_valid(self, form):
        form.instance.farmer = self.request.user
        return super(CreateProfile, self).form_valid(form)


class UpdateProfile(UpdateView):
    model = Profile
    form_class = ProfileForm
    success_url = "/home"

    def get_form(self, form_class=None):
        form = super(UpdateProfile, self).get_form(form_class)
        if self.request.user in form.fields["supervisor"].queryset:
            form.fields["supervisor"].queryset = form.fields[
                "supervisor"
            ].queryset.exclude(pk=self.request.user.id)
        return form

    def get_context_data(self, **kwargs):
        context = super(UpdateProfile, self).get_context_data(**kwargs)
        profile = Profile.objects.get(pk=self.kwargs["pk"])
        if not self.request.user == profile.farmer:
            raise Http404
        return context


class DeleteProfile(DeleteView):
    model = Profile

    def get_success_url(self):
        profile = Profile.objects.get(pk=self.kwargs["pk"])
        user = User.objects.get(pk=profile.farmer.id)
        # Delete all user data using bult in cascade delete
        user.delete()
        return reverse("welcome")


class CreateAgrifield(CreateView):
    model = Agrifield
    form_class = AgrifieldForm

    def form_valid(self, form):
        user = User.objects.get(username=self.kwargs["username"])
        form.instance.owner = user
        return super(CreateAgrifield, self).form_valid(form)

    def get_success_url(self):
        url_username = self.kwargs["username"]
        return reverse("home", kwargs={"username": url_username})

    def get_context_data(self, **kwargs):
        context = super(CreateAgrifield, self).get_context_data(**kwargs)
        try:
            url_username = self.kwargs["username"]
            user = User.objects.get(username=url_username)
            context["agrifields"] = Agrifield.objects.filter(owner=user).all()
            context["fields_count"] = context["agrifields"].count()
            context["agrifield_user"] = user

        except Agrifield.DoesNotExist:
            context["agrifields"] = None
        return context


class UpdateAgrifield(UpdateView):
    model = Agrifield
    form_class = AgrifieldForm
    template_name = "aira/agrifield_update.html"

    def get_success_url(self):
        field = Agrifield.objects.get(pk=self.kwargs["pk"])
        return reverse("home", kwargs={"username": field.owner})

    def get_context_data(self, **kwargs):
        context = super(UpdateAgrifield, self).get_context_data(**kwargs)
        afieldobj = Agrifield.objects.get(pk=self.kwargs["pk"])
        afieldobj.can_edit(self.request.user)

        # These are a voodoo fix that should go away when we go to Leaflet and
        # cleanup all this part.
        afieldobj.location.transform("EPSG:4326")
        self.object.location.transform("EPSG:4326")

        context["agrifield_user"] = afieldobj.owner
        if agripoint_in_raster(afieldobj):
            context["default_parms"] = get_default_db_value(afieldobj)
        return context


class DeleteAgrifield(DeleteView):
    model = Agrifield
    form_class = AgrifieldForm

    def get_success_url(self):
        field = Agrifield.objects.get(pk=self.kwargs["pk"])
        return reverse("home", kwargs={"username": field.owner})

    def get_context_data(self, **kwargs):
        context = super(DeleteAgrifield, self).get_context_data(**kwargs)
        afieldobj = Agrifield.objects.get(pk=self.kwargs["pk"])
        afieldobj.can_edit(self.request.user)
        return context


class CreateIrrigationLog(CreateView):
    model = IrrigationLog
    form_class = IrrigationlogForm
    success_url = "/home"

    def get_success_url(self):
        field = Agrifield.objects.get(pk=self.kwargs["pk"])
        return reverse("home", kwargs={"username": field.owner})

    def form_valid(self, form):
        form.instance.agrifield = Agrifield.objects.get(pk=self.kwargs["pk"])
        return super(CreateIrrigationLog, self).form_valid(form)

    def get_context_data(self, **kwargs):
        context = super(CreateIrrigationLog, self).get_context_data(**kwargs)
        try:
            context["agrifield"] = Agrifield.objects.get(pk=self.kwargs["pk"])
            afieldobj = Agrifield.objects.get(pk=self.kwargs["pk"])
            afieldobj.can_edit(self.request.user)
            context["logs"] = IrrigationLog.objects.filter(agrifield=afieldobj).all()
            context["logs_count"] = context["logs"].count()
            context["agrifield_user"] = afieldobj.owner
        except Agrifield.DoesNotExist:
            context["logs"] = None
        return context


class UpdateIrrigationLog(UpdateView):
    model = IrrigationLog
    form_class = IrrigationlogForm
    template_name = "aira/irrigationlog_update.html"

    def get_success_url(self):
        field = Agrifield.objects.get(pk=self.kwargs["pk_a"])
        return reverse("home", kwargs={"username": field.owner})

    def get_context_data(self, **kwargs):
        context = super(UpdateIrrigationLog, self).get_context_data(**kwargs)
        afieldobj = Agrifield.objects.get(pk=self.kwargs["pk_a"])
        afieldobj.can_edit(self.request.user)
        log = IrrigationLog.objects.get(pk=self.kwargs["pk"])
        log.can_edit(afieldobj)
        context["agrifield_id"] = afieldobj.id
        return context


class DeleteIrrigationLog(DeleteView):
    model = IrrigationLog
    form_class = IrrigationlogForm

    def get_success_url(self):
        field = Agrifield.objects.get(pk=self.kwargs["pk_a"])
        return reverse("home", kwargs={"username": field.owner})


def remove_supervised_user_from_user_list(request):
    # Called in templates:aira:home
    if request.is_ajax() and request.POST:
        supervised_profile = Profile.objects.get(
            pk=int(request.POST.get("supervised_id"))
        )
        supervised_profile.supervisor = None
        supervised_profile.save()
        response_data = {"message": "Success!!!"}
        return HttpResponse(json.dumps(response_data), content_type="application/json")
    raise Http404


class AgrifieldTimeseries(View):
    def get(self, *args, **kwargs):
        filename = self._get_point_timeseries(*args, **kwargs)
        return FileResponse(
            open(filename, "rb"), as_attachment=True, content_type="text_csv"
        )

    def _get_point_timeseries(self, *args, **kwargs):
        agrifield = get_object_or_404(Agrifield, pk=kwargs.get("agrifield_id"))
        variable = kwargs.get("variable")
        prefix = os.path.join(settings.AIRA_DATA_HISTORICAL, "daily_" + variable)
        dest = os.path.join(
            settings.AIRA_TIMESERIES_CACHE_DIR,
            "agrifield{}-{}.hts".format(agrifield.id, variable),
        )
        PointTimeseries(point=agrifield.location, prefix=prefix).get_cached(
            dest, version=2
        )
        return dest


class DownloadSoilAnalysis(View):
    def get(self, *args, **kwargs):
        agrifield = get_object_or_404(Agrifield, pk=kwargs.get("agrifield_id"))
        agrifield.can_edit(self.request.user)
        if not agrifield.soil_analysis:
            raise Http404
        return FileResponse(agrifield.soil_analysis, as_attachment=True)
