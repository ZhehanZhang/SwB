from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.sdist import sdist


def require_web_build() -> None:
    if not Path(__file__).with_name("src").joinpath("switchboard/static/app/index.html").is_file():
        raise SystemExit("Build the web UI with npm run build --prefix web before packaging SwB.")


class Wheel(bdist_wheel):
    def run(self) -> None:
        require_web_build()
        super().run()


class SourceDist(sdist):
    def run(self) -> None:
        require_web_build()
        super().run()


setup(cmdclass={"bdist_wheel": Wheel, "sdist": SourceDist})
