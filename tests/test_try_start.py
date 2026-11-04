"""The profile reading of `hox try-start`; the rest runs on a node."""

import pytest

from hox_cli.try_start import Profile, parse

PROFILE = """\
[buildout]
parts = a

# A comment.
[directory]
home = ${buildout:directory}
etc = ${:home}/etc

[app-parameters]
init =
  n = int(self.buildout['slap-connection']['partition-id'][len('slappart'):])
  if n:
    options['port'] = str(30000 + n)
host = ${slap-configuration:ipv4-random}

[secret]
recipe = slapos.cookbook:generate.password

[process-base]
environment =
  HOME=${directory:home}
  PORT=${app-parameters:port}
  KEY=${secret:passwd}
  LITERAL=$${NOT:THIS}

[web]
<= process-base
command = run ${:environment}

[env]
recipe = slapos.recipe.template
output = ${directory:home}/app.env
inline =
  export A=1
  ${directory:etc}

[parameters]
recipe = slapos.recipe.template:jinja2
output = ${directory:home}/parameters.env
context = key parameters slap-configuration:configuration
inline =
  {%- for name in parameters %}
"""


@pytest.fixture
def profile(tmp_path):
    return Profile(PROFILE, {("buildout", "directory"): str(tmp_path),
                             ("slap-configuration", "ipv4-random"): "127.0.0.1"})


def test_parse_keeps_the_indentation_of_a_value():
    assert parse(PROFILE)["app-parameters"]["init"].splitlines()[2] == "  if n:"


def test_parse_leaves_out_the_blank_lines_after_a_value():
    assert parse(PROFILE)["secret"]["recipe"] == "slapos.cookbook:generate.password"


def test_references_recipe_code_and_passwords(profile, tmp_path):
    environment = profile.environment()
    assert environment["HOME"] == str(tmp_path)
    assert environment["PORT"] == "30099"  # from the section's init
    assert len(environment["KEY"]) > 20
    assert environment["KEY"] == profile.get("secret", "passwd")  # generated once
    assert environment["LITERAL"] == "${NOT:THIS}"
    assert profile.get("directory", "etc") == f"{tmp_path}/etc"


def test_a_section_inherits(profile):
    assert profile.get("web", "command").startswith("run \n")


def test_asking_for_output_writes_the_file(profile, tmp_path):
    assert profile.get("env", "output") == f"{tmp_path}/app.env"
    assert (tmp_path / "app.env").read_text() == f"export A=1\n{tmp_path}/etc\n"


def test_instance_parameters_are_empty(profile, tmp_path):
    profile.get("parameters", "output")
    assert (tmp_path / "parameters.env").read_text() == "\n"


def test_an_unknown_reference_says_which(profile):
    with pytest.raises(KeyError, match="nope:thing"):
        profile.get("nope", "thing")
