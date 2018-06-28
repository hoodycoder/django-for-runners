"""
    created 30.05.2018 by Jens Diemer <opensource@jensdiemer.de>
    :copyleft: 2018 by the django-for-runners team, see AUTHORS for more details.
    :license: GNU GPL v3 or above, see LICENSE for more details.
"""
import io
import logging
import statistics

from django.conf import settings
from django.core.files import File
from django.db import models
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _
from django_tools.decorators import display_admin_error
from django_tools.models import UpdateTimeBaseModel
from filer.fields.file import FilerFileField
from filer.utils.loader import load_model
from for_runners.geo import reverse_geo
from for_runners.gpx import (add_extension_data, get_2d_coordinate_list,
                             get_extension_data, get_identifier, iter_distance,
                             iter_points, parse_gpx)
from for_runners.gpx_tools.humanize import human_distance, human_seconds
from for_runners.managers.gpx import GpxModelManager
from for_runners.models import DistanceModel, EventModel
from for_runners.svg import gpx2svg_string
from for_runners.weather import NoWeatherData, meta_weather_com

log = logging.getLogger(__name__)


class GpxModel(UpdateTimeBaseModel):
    """
    inherit from UpdateTimeBaseModel:
        * createtime
        * lastupdatetime
    """
    event = models.ForeignKey(
        EventModel,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    gpx = models.TextField(help_text="The raw gpx file content", )
    creator = models.CharField(
        help_text="Used device to create this track",
        max_length=511,
        null=True,
        blank=True,
    )
    track_svg = FilerFileField(
        verbose_name=_("Track SVG"),
        related_name="+",
        null=True,
        blank=True,
    )

    start_time = models.DateTimeField(
        editable=False,
        help_text=_("Start time of the first segment in track"),
    )
    start_latitude = models.FloatField(
        editable=False,
        help_text=_(
            "Latitude of the first recorded point from the *.gpx file"),
    )
    start_longitude = models.FloatField(
        editable=False,
        help_text=_(
            "Longitude of the first recorded point from the *.gpx file"),
    )
    start_temperature = models.FloatField(
        editable=True,
        null=True,
        blank=True,
        help_text=_("Temperature at start."),
    )
    start_weather_state = models.CharField(
        max_length=127,
        null=True,
        blank=True,
        help_text="Weather state at start.",
    )
    short_start_address = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="The short address of the start point",
    )
    full_start_address = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="The full address of the start point",
    )

    finish_time = models.DateTimeField(
        editable=False,
        help_text=_("End time of the last segment in track"),
    )
    finish_latitude = models.FloatField(
        editable=False,
        help_text=_("Latitude of the finish point"),
    )
    finish_longitude = models.FloatField(
        editable=False,
        help_text=_("Longitude of the finish point"),
    )
    finish_temperature = models.FloatField(
        editable=True,
        null=True,
        blank=True,
        help_text=_("Temperature at finish."),
    )
    finish_weather_state = models.CharField(
        max_length=127,
        null=True,
        blank=True,
        help_text="Weather state at finish.",
    )
    short_finish_address = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="The short address of the finish point",
    )
    full_finish_address = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="The full address of the finish point",
    )

    tracked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        editable=False,
        related_name="%(class)s_createby",
        null=True,
        blank=True,
        help_text="The user that tracked this gpx entry",
        on_delete=models.SET_NULL)
    lastupdateby = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        editable=False,
        related_name="%(class)s_lastupdateby",
        null=True,
        blank=True,
        help_text="User as last edit this entry",
        on_delete=models.SET_NULL)

    points_no = models.PositiveIntegerField(
        help_text=_("Number of points in GPX"),
        null=True,
        blank=True,
    )

    length = models.PositiveIntegerField(
        help_text=
        _("Length in meters (calculated 3-dimensional used latitude, longitude, and elevation)"
          ),
        null=True,
        blank=True,
    )
    ideal_distance = models.ForeignKey(
        to=DistanceModel,
        on_delete=models.SET_NULL,
        related_name="tracks",
        help_text=
        _("Length in meters (calculated 3-dimensional used latitude, longitude, and elevation)"
          ),
        null=True,
        blank=True,
    )

    duration = models.PositiveIntegerField(
        help_text=_("Duration in seconds"),
        null=True,
        blank=True,
    )
    net_duration = models.TimeField(
        verbose_name=_("Net Duration"),
        help_text=
        _("The officially measured time (e.g. from the official timekeeping of a running event.)"
          ),
        null=True,
        blank=True,
    )
    pace = models.DecimalField(
        help_text=_(
            "Min/km (number of minutes it takes to cover a kilometer)"),
        max_digits=4,
        decimal_places=
        2,  # store numbers up to 99 with a resolution of 2 decimal places
        null=True,
        blank=True,
    )

    uphill = models.IntegerField(
        help_text=_("Uphill elevation climbs in meters"),
        null=True,
        blank=True,
    )
    downhill = models.IntegerField(
        help_text=_("Downhill elevation descent in meters"),
        null=True,
        blank=True,
    )

    min_elevation = models.IntegerField(
        help_text=_("Minimum elevation in meters"),
        null=True,
        blank=True,
    )
    max_elevation = models.IntegerField(
        help_text=_("Maximum elevation in meters"),
        null=True,
        blank=True,
    )

    heart_rate_min = models.PositiveIntegerField(
        help_text=_("Minimum heart rate."),
        null=True,
        blank=True,
        editable=False)
    heart_rate_avg = models.PositiveIntegerField(
        help_text=_("Average heart rate."),
        null=True,
        blank=True,
        editable=False)
    heart_rate_max = models.PositiveIntegerField(
        help_text=_("Maximum heart rate."),
        null=True,
        blank=True,
        editable=False)

    objects = GpxModelManager()

    def save(self, *args, **kwargs):
        if self.gpx:
            self.calculate_values()

        super().save(*args, **kwargs)

        # TODO: schedule request weather info, if not set

    def svg_tag(self):
        if self.track_svg:
            return '<img src="{}" alt="gpx track" height="70px" width="70px" />'.format(
                self.track_svg.url)
        return ""

    svg_tag.short_description = _("SVG")
    svg_tag.allow_tags = True

    def svg_tag_big(self):
        if self.track_svg:
            return '<img src="{}" alt="gpx track" height="200px" width="200px" />'.format(
                self.track_svg.url)
        return ""

    svg_tag_big.short_description = _("SVG")
    svg_tag_big.allow_tags = True

    def start_end_address(self):
        if self.short_start_address == self.short_finish_address:
            return "\u27F3 %s" % self.short_start_address
        return "%s<br>\u25BE<br>%s" % (self.short_start_address,
                                       self.short_finish_address)

    start_end_address.short_description = _("Start/End Address")
    start_end_address.allow_tags = True

    def get_ideal_ratio(self):
        if self.ideal_distance:
            ratio = (
                float(self.ideal_distance.distance_km) * 1000) / self.length
            return ratio

    def get_net_duration_s(self):
        """
        :return: net duration in seconds
        """
        if self.net_duration:
            # FIXME: Is there really no easier way to do this?
            duration = self.net_duration.second
            duration += (self.net_duration.minute * 60)
            duration += (self.net_duration.hour * 60 * 60)
            return duration

    def get_ideal_duration(self):
        ratio = self.get_ideal_ratio()
        if ratio:
            return self.duration * ratio

    def get_ideal_pace(self):
        ratio = self.get_ideal_ratio()
        if ratio:
            return self.pace * ratio

    def get_ideal_distance_diff_m(self):
        if self.ideal_distance:
            distance_diff_m = (
                float(self.ideal_distance.distance_km) * 1000) - self.length
            return distance_diff_m

    def human_ideal_length(self):
        """
        used as labels in chart.js
        """
        if self.ideal_distance:
            return self.ideal_distance.get_human_distance()
        return human_distance(self.length / 1000)

    def human_length(self):
        if self.length:
            length_km = self.length / 1000
            html = ('<span title="real distance">%s</span>'
                    ) % human_distance(length_km)

            if self.ideal_distance:
                diff_km = abs(self.get_ideal_distance_diff_m() / 1000)
                html = ('<span title="standardized distance">%s</span>'
                        '<br>'
                        '(%s)'
                        '<br>'
                        'diff: %s') % (
                            self.ideal_distance.get_human_distance(), html,
                            human_distance(diff_km))

            return mark_safe(html)

    human_length.short_description = _("Length")
    human_length.admin_order_field = "length"

    def human_duration(self):
        # TODO: use a template for this
        html = "-"
        if self.duration:
            html = ('<span title="real duration">%s</span>') % human_seconds(
                self.duration)

        if self.net_duration:
            net_duration_s = self.get_net_duration_s()
            duration_diff = self.duration - net_duration_s
            html = ('<span title="Official net duration">%s</span>'
                    '<br>'
                    '(%s)'
                    '<br>'
                    'diff: %s') % (
                        human_seconds(net_duration_s),
                        html,
                        human_seconds(duration_diff),
                    )
        else:
            ideal_duration = self.get_ideal_duration()
            if ideal_duration:
                duration_diff = self.duration - ideal_duration
                html = ('<span title="standardized duration">%s</span>'
                        '<br>'
                        '(%s)'
                        '<br>'
                        'diff: %s') % (
                            human_seconds(ideal_duration),
                            html,
                            human_seconds(duration_diff),
                        )

        return html

    human_duration.short_description = _("Duration")
    human_duration.allow_tags = True
    human_duration.admin_order_field = "duration"

    def human_pace(self):
        if self.pace:

            return "%s min/km" % human_seconds(self.pace * 60)

    human_pace.short_description = _("Pace")
    human_pace.admin_order_field = "pace"

    def human_weather(self):
        if not self.start_temperature:
            return "-"
        return "%s°C<br/>%s" % (round(self.start_temperature, 1),
                                self.start_weather_state)

    human_weather.short_description = _("Weather")
    human_weather.admin_order_field = "start_temperature"
    human_weather.allow_tags = True

    def _coordinate2link(self, lat, lon):
        return (
            '<a'
            ' href="https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&addressdetails=1"'
            ' title="Reverse {lat},{lon} address with OpenStreepMap"'
            ' target="_blank"'
            '>reverse address</a>'
            '<br>'
            '<a'
            ' href="https://www.openstreetmap.org/search?query={lat}%2C{lon}"'
            ' title="OpenStreepMap at {lat},{lon}"'
            ' target="_blank"'
            '>map</a>').format(
                lat=lat, lon=lon)

    def start_coordinate_html(self):
        """
        return HTML Links for start point.
        """
        if self.start_latitude and self.start_longitude:
            return self._coordinate2link(
                lat=self.start_latitude,
                lon=self.start_longitude,
            )

    start_coordinate_html.short_description = _("Start coordinates")
    start_coordinate_html.allow_tags = True

    def finish_coordinate_html(self):
        """
        return HTML Links for finish point.
        """
        if self.finish_latitude and self.finish_longitude:
            return self._coordinate2link(
                lat=self.finish_latitude,
                lon=self.finish_longitude,
            )

    finish_coordinate_html.short_description = _("finish coordinates")
    finish_coordinate_html.allow_tags = True

    def leaflet_map_html(self):
        gpxpy_instance = self.get_gpxpy_instance()

        lat_list, lon_list = get_2d_coordinate_list(gpxpy_instance)
        coordinates = zip(lat_list, lon_list)

        km_gpx_points = iter_distance(gpxpy_instance, distance=1000)

        context = {
            "short_start_address": self.short_start_address,
            "start_time": self.start_time,
            "start_latitude": self.start_latitude,
            "start_longitude": self.start_longitude,
            "short_finish_address": self.short_finish_address,
            "finish_time": self.finish_time,
            "finish_latitude": self.finish_latitude,
            "finish_longitude": self.finish_longitude,
            "coordinates": coordinates,
            "km_gpx_points": km_gpx_points,
        }
        return render_to_string(
            template_name="for_runners/leaflet_map.html", context=context)

    leaflet_map_html.short_description = _("Leaflet MAP")
    leaflet_map_html.allow_tags = True

    @display_admin_error
    def chartjs_html(self):
        gpxpy_instance = self.get_gpxpy_instance()

        labels = []
        elevations = []
        heart_rates = []
        cadence_values = []

        for point in iter_points(gpxpy_instance):
            add_extension_data(point)
            labels.append(point.time)
            elevations.append(point.elevation)
            try:
                heart_rates.append(point.extension_data["hr"])
            except KeyError:
                pass
            try:
                cadence_values.append(point.extension_data["cad"])
            except KeyError:
                pass

        context = {
            "instance": self,
            "labels": labels,
            "elevations": elevations,
            "heart_rates": heart_rates,
            "cadence_values": cadence_values,
        }
        return render_to_string(
            template_name="for_runners/chartjs.html", context=context)

    chartjs_html.short_description = _("chartjs MAP")
    chartjs_html.allow_tags = True

    def point_density(self):
        """
        Calculate the "density" of the GPX signal:
        :return: float - Meters/point count
        """
        if self.length > 0:
            density = self.length / self.points_no
            return density

    def gpx_meta(self):
        gpxpy_instance = self.get_gpxpy_instance()
        attr_names = (
            "version",
            "creator",
            "name",
            "description",
            "author_name",
            "author_email",
            "author_link",
            "author_link_text",
            "author_link_type",
            "copyright_author",
            "copyright_year",
            "copyright_license",
            "link",
            "link_text",
            "link_type",
            "time",
            "keywords",
        )
        result = []
        for attr_name in attr_names:
            value = getattr(gpxpy_instance, attr_name, None)
            if value:
                result.append((attr_name, value))

        return result

    _GPXPY_CACHE = {}

    def get_gpxpy_instance(self):
        try:
            return self._GPXPY_CACHE[self.pk]
        except KeyError:
            if self.gpx:
                gpxpy_instance = parse_gpx(content=self.gpx)
                if self.pk is not None:
                    self._GPXPY_CACHE[self.pk] = gpxpy_instance
                return gpxpy_instance

    def calc_pace(self):
        duration_s = self.get_net_duration_s()
        if not duration_s:
            duration_s = self.get_ideal_duration()
        if not duration_s:
            duration_s = self.duration
        if not duration_s:
            return None

        if self.ideal_distance:
            distance_km = int(self.ideal_distance.distance_km)
        else:
            distance_km = self.length / 1000

        pace = (duration_s / 60) / distance_km
        if pace > 99 or pace < 0:
            log.error("Pace out of range: %f", pace)
        else:
            self.pace = pace

    def calculate_values(self):
        if not self.gpx:
            return

        gpxpy_instance = self.get_gpxpy_instance()
        self.points_no = gpxpy_instance.get_points_no()
        self.length = gpxpy_instance.length_3d()

        try:
            self.ideal_distance = DistanceModel.objects.get(
                min_distance_m__lte=self.length,
                max_distance_m__gte=self.length,
            )
        except DistanceModel.DoesNotExist:
            pass
        else:
            log.debug("Set ideal distance to %s", self.ideal_distance)

        # e.g: GPX without a track return 0
        duration = gpxpy_instance.get_duration()
        if duration:
            self.duration = duration
            self.calc_pace()

        uphill_downhill = gpxpy_instance.get_uphill_downhill()
        self.uphill = uphill_downhill.uphill
        self.downhill = uphill_downhill.downhill

        elevation_extremes = gpxpy_instance.get_elevation_extremes()
        self.min_elevation = elevation_extremes.minimum
        self.max_elevation = elevation_extremes.maximum

        identifier = get_identifier(gpxpy_instance)

        self.start_time = identifier.start_time
        self.finish_time = identifier.finish_time
        self.start_latitude = identifier.start_lat
        self.start_longitude = identifier.start_lon
        self.finish_latitude = identifier.finish_lat
        self.finish_longitude = identifier.finish_lon

        if not self.start_temperature:
            try:
                temperature, weather_state = meta_weather_com.coordinates2weather(
                    self.start_latitude,
                    self.start_longitude,
                    date=self.start_time,
                    max_seconds=self.duration)
            except NoWeatherData:
                log.error("No weather data for start.")
            else:
                self.start_temperature = temperature
                self.start_weather_state = weather_state

        if not self.finish_temperature:
            try:
                temperature, weather_state = meta_weather_com.coordinates2weather(
                    self.finish_latitude,
                    self.finish_longitude,
                    date=self.finish_time,
                    max_seconds=self.duration)
            except NoWeatherData:
                log.error("No weather data for finish.")
            else:
                self.finish_temperature = temperature
                self.finish_weather_state = weather_state

        if not self.full_start_address:
            try:
                start_address = reverse_geo(self.start_latitude,
                                            self.start_longitude)
            except Exception as err:
                # e.g.: geopy.exc.GeocoderTimedOut: Service timed out
                log.error("Can't reverse geo: %s" % err)
            else:
                self.short_start_address = start_address.short
                self.full_start_address = start_address.full

        if not self.full_finish_address:
            try:
                finish_address = reverse_geo(self.finish_latitude,
                                             self.finish_longitude)
            except Exception as err:
                # e.g.: geopy.exc.GeocoderTimedOut: Service timed out
                log.error("Can't reverse geo: %s" % err)
            else:
                self.short_finish_address = finish_address.short
                self.full_finish_address = finish_address.full

        if not self.track_svg:
            log.debug("Create SVG from GPX...")

            svg_string = gpx2svg_string(gpxpy_instance)

            # import filer.models.imagemodels.Image
            Image = load_model(settings.FILER_IMAGE_MODEL)

            temp = io.BytesIO(bytes(svg_string, "utf-8"))
            django_file_obj = File(temp, name="gpx.svg")
            filer_image = Image.objects.create(
                owner=self.tracked_by,
                original_filename="gpx.svg",
                file=django_file_obj,
                folder=None)
            filer_image.save()

            # self.track_svg.save("gpx2svg", svg_string)
            self.track_svg = filer_image  #save("gpx2svg", svg_string)

        # TODO: Handle other extensions, too.
        # Garmin containes also 'cad'
        extension_data = get_extension_data(gpxpy_instance)
        if extension_data is not None and "hr" in extension_data:
            heart_rates = extension_data["hr"]
            self.heart_rate_min = min(heart_rates)
            self.heart_rate_avg = statistics.median(heart_rates)
            self.heart_rate_max = max(heart_rates)

        if not self.creator:
            self.creator = gpxpy_instance.creator

    def short_name(self):
        if self.pk is None:
            return "new, unsaved GPX Track"

        parts = [self.start_time.strftime("%Y-%m-%d")]
        if self.event:
            parts.append(self.event.name)
        else:
            parts.append(self.short_start_address)
        result = " ".join([str(part) for part in parts if part])
        if result:
            return result
        return "GPX Track ID:%s" % self.pk

    def get_short_slug(self):
        name = self.short_name()
        return slugify(name)

    def __str__(self):
        return self.short_name()

    class Meta:
        verbose_name = _('GPX Track')
        verbose_name_plural = _('GPX Tracks')
        unique_together = (("start_time", "start_latitude", "start_longitude",
                            "finish_time", "finish_latitude",
                            "finish_longitude"), )
        ordering = ('-start_time', '-pk')