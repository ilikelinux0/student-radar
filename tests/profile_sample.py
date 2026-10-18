"""A made-up profile for tests. Tests never read the owner's profile.yaml."""

from radar.profile import Profile, load_profile

PROFILE_YAML = """\
fit_summary: >
  Final-year computer engineering student focused on offensive security,
  looking for security internships and new-grad programs.
private_terms: [Zephyrcorp]
facts:
  birth_date: 2001-02-03
  citizenship: [tr]
  passport: false
  work_authorization: [TR]
  graduation: 2027-06
  gpa: 3.17
  english: C1
  military_service: deferred
  home_city: Istanbul
"""


def sample_profile() -> Profile:
    profile = load_profile({"RADAR_PROFILE": PROFILE_YAML})
    assert profile is not None
    return profile
