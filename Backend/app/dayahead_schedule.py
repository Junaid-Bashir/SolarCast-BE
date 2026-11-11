"""Brussels day-ahead calendar and recursive timeline; no actuals or model inputs."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

STEP = pd.Timedelta(900_000_000_000, unit='ns')
ZONE = ZoneInfo('Europe/Brussels')


def validate_delay(minutes):
    if type(minutes) is not int or minutes < 0:
        raise ValueError('Availability delay must be a nonnegative integer number of minutes')


def delay_delta(minutes):
    validate_delay(minutes)
    return pd.Timedelta(minutes * 60_000_000_000, unit='ns')


def utc(value):
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError('Explicit UTC offsets are required')
    return timestamp.tz_convert('UTC')


def local_midnight(day):
    if type(day) is not date:
        raise ValueError('Delivery dates must be calendar dates, not datetimes')
    return pd.Timestamp(datetime.combine(day, time(), ZONE)).tz_convert('UTC')


def targets_for_day(day):
    return pd.date_range(local_midnight(day), local_midnight(day + timedelta(days=1)),
                         freq=STEP, inclusive='left')


@dataclass(frozen=True)
class DayAheadWindow:
    delivery_date: date
    issue_at: pd.Timestamp
    observation_origin: pd.Timestamp
    delay_minutes: int

    @property
    def delivery_start(self):
        return local_midnight(self.delivery_date)

    @property
    def delivery_end_exclusive(self):
        return local_midnight(self.delivery_date + timedelta(days=1))

    @property
    def targets(self):
        return targets_for_day(self.delivery_date)

    @property
    def trajectory(self):
        return pd.date_range(self.observation_origin + STEP, self.delivery_end_exclusive,
                             freq=STEP, inclusive='left')

    @property
    def bridge(self):
        times = self.trajectory
        return times[times < self.delivery_start]

    def metadata(self):
        targets = self.targets
        return {'delivery_date': self.delivery_date.isoformat(),
                'issue_at': self.issue_at.isoformat(),
                'observation_origin': self.observation_origin.isoformat(),
                'origin_available_at_assumed': (self.observation_origin + STEP + delay_delta(self.delay_minutes)).isoformat(),
                'delivery_start': self.delivery_start.isoformat(),
                'delivery_end_exclusive': self.delivery_end_exclusive.isoformat(),
                'first_delivery_target': targets[0].isoformat(),
                'last_delivery_target': targets[-1].isoformat(),
                'delivery_intervals': len(targets), 'bridge_intervals': len(self.bridge),
                'recursive_steps': len(self.trajectory),
                'first_delivery_lead_from_issue_minutes': int((targets[0]-self.issue_at)/pd.Timedelta(60_000_000_000,unit='ns')),
                'last_delivery_lead_from_issue_minutes': int((targets[-1]-self.issue_at)/pd.Timedelta(60_000_000_000,unit='ns')),
                'last_delivery_lead_from_origin_minutes': int((targets[-1]-self.observation_origin)/pd.Timedelta(60_000_000_000,unit='ns')),
                'availability_delay_minutes_after_interval_end_assumed': self.delay_minutes}

    def points(self):
        for step, target in enumerate(self.trajectory, 1):
            yield {'delivery_date': self.delivery_date.isoformat(),
                   'issue_at': self.issue_at.isoformat(),
                   'observation_origin': self.observation_origin.isoformat(),
                   'forecast_step': step,
                   'role': 'delivery' if target >= self.delivery_start else 'bridge',
                   'target_timestamp_utc': target.isoformat(),
                   'target_timestamp_local': target.tz_convert(ZONE).isoformat(),
                   'interval_end_utc': (target+STEP).isoformat(),
                   'lead_from_issue_minutes': int((target-self.issue_at)/pd.Timedelta(60_000_000_000,unit='ns')),
                   'lead_from_origin_minutes': step*15}


def window_for_day(day, delay_minutes):
    local_midnight(day)  # Validate the public date argument.
    delay = delay_delta(delay_minutes)
    issue = pd.Timestamp(datetime.combine(day-timedelta(days=1), time(18), ZONE)).tz_convert('UTC')
    origin = (issue-STEP-delay).floor('15min')
    return DayAheadWindow(day, issue, origin, delay_minutes)
