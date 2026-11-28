import sys
from pathlib import Path, PurePosixPath
from tarfile import open as tar_open
from zipfile import ZipFile


def check_dist(archive: Path, paths: list[PurePosixPath]) -> None:
    excluded = [
        path for path in paths
        if "__screenshots__" in path.parts or ("web", "tests") in zip(path.parts, path.parts[1:])
    ]
    if excluded:
        raise SystemExit(f"{archive.name} includes test files: {', '.join(map(str, excluded[:5]))}")
    if not any(path.parts[-4:] == ("switchboard", "static", "app", "index.html") for path in paths):
        raise SystemExit(f"{archive.name} is missing the console build")
    print(f"{archive.name}: {len(paths)} files, console present, web tests and visual baselines absent")


def main(dist: Path) -> None:
    wheels = sorted(dist.glob("*.whl"))
    sources = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise SystemExit(f"Expected one wheel and one sdist in {dist}")
    with ZipFile(wheels[0]) as wheel:
        check_dist(wheels[0], [PurePosixPath(member.filename) for member in wheel.infolist() if not member.is_dir()])
    with tar_open(sources[0]) as source:
        check_dist(sources[0], [PurePosixPath(*PurePosixPath(member.name).parts[1:]) for member in source if member.isfile()])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
