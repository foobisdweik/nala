#                 __
#    ____ _____  |  | _____
#   /    \\__  \ |  | \__  \
#  |   |  \/ __ \|  |__/ __ \_
#  |___|  (____  /____(____  /
#       \/     \/          \/
#
# Copyright (C) 2021, 2022 Blake Lee
#
# This file is part of nala
#
# nala is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# nala is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with nala.  If not, see <https://www.gnu.org/licenses/>.
"""Compatibility commands making Nala a safe drop-in replacement for apt/apt-get/apt-mark/apt-cache.

This module adds the commands that are present in apt, apt-get, apt-mark, and apt-cache
but were previously missing from Nala. With these added, Nala can be aliased to apt/apt-get
without any commands failing due to "unknown command" errors.

New commands provided:
  reinstall        - Reinstall packages (apt-get reinstall)
  autoclean        - Remove outdated cached .deb files (apt-get autoclean)
  check            - Verify no broken dependencies exist (apt-get check)
  depends          - Show package dependency tree (apt-cache depends)
  rdepends         - Show reverse dependencies (apt-cache rdepends)
  policy           - Show package priorities and candidate versions (apt-cache policy)
  download         - Download .deb files to current directory (apt-get download)
  build-dep        - Install build dependencies (apt-get build-dep)
  satisfy          - Satisfy arbitrary dependency strings (apt-get satisfy)
  source           - Download source packages (apt-get source)
  changelog        - Fetch and display changelogs (apt-get changelog)
  edit-sources     - Open sources.list in an editor (apt edit-sources)
  hold             - Hold a package at its current version (apt-mark hold)
  unhold           - Remove a hold from a package (apt-mark unhold)
  showhold         - List all held packages (apt-mark showhold)
  mark             - Mark packages as auto/manual (apt-mark auto/manual)
  showauto         - List automatically installed packages (apt-mark showauto)
  showmanual       - List manually installed packages (apt-mark showmanual)
  minimize-manual  - Mark meta-package deps as auto (apt-mark minimize-manual)
"""
from __future__ import annotations

import sys
from pathlib import Path
from subprocess import CalledProcessError, run
from typing import List, Optional

import apt_pkg
import typer

from nala import _, color
from nala.cache import Cache
from nala.constants import ARCHIVE_DIR, ERROR_PREFIX
from nala.error import pkg_error
from nala.install import (
	check_state,
	get_changes,
	setup_cache,
)
from nala.nala import nala_pkgs, package_completion, remove_completion
from nala.options import (
	ASSUME_YES,
	COLOR,
	DEBUG,
	DOWNLOAD_ONLY,
	FIX_BROKEN,
	MAN_HELP,
	OPTION,
	RAW_DPKG,
	RECOMMENDS,
	REMOVE_ESSENTIAL,
	SIMPLE,
	SUGGESTS,
	UPDATE,
	VERBOSE,
	arguments,
	nala,
)
from nala.rich import ELLIPSIS
from nala.utils import (
	dedupe_list,
	eprint,
	get_pkg_name,
	sudo_check,
	unit_str,
	vprint,
)

# pylint: disable=unused-argument,too-many-arguments,too-many-locals


# ── reinstall ────────────────────────────────────────────────────────────────

@nala.command(help=_("Reinstall packages."))
def reinstall(
	ctx: typer.Context,
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to reinstall"),
		autocompletion=remove_completion,
	),
	debug: bool = DEBUG,
	raw_dpkg: bool = RAW_DPKG,
	download_only: bool = DOWNLOAD_ONLY,
	remove_essential: bool = REMOVE_ESSENTIAL,
	update: bool = UPDATE,
	install_recommends: bool = RECOMMENDS,
	install_suggests: bool = SUGGESTS,
	fix_broken: bool = FIX_BROKEN,
	assume_yes: bool = ASSUME_YES,
	simple: bool = SIMPLE,
	dpkg_option: List[str] = OPTION,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Reinstall packages, or install them if not currently installed."""
	sudo_check()
	cache = setup_cache()
	check_state(cache, nala_pkgs)

	not_found: list[str] = []
	for pkg_name in dedupe_list(pkg_names):
		if pkg_name not in cache:
			not_found.append(pkg_name)
			continue
		pkg = cache[pkg_name]
		if not pkg.installed:
			eprint(
				_("{notice} {package} is not installed, installing instead.").format(
					notice=color(_("Notice:"), "YELLOW"),
					package=color(pkg_name, "GREEN"),
				)
			)
		pkg.mark_install(from_user=True)

	if not_found:
		pkg_error(not_found, cache)

	get_changes(cache, nala_pkgs, "install")


# ── autoclean ────────────────────────────────────────────────────────────────

@nala.command(help=_("Remove old downloaded package files."))
def autoclean(
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Remove downloaded package files that can no longer be downloaded or are outdated.

	Unlike 'clean', autoclean only removes package files that can no longer be
	downloaded (packages that have been superseded in the repository).
	"""
	sudo_check()
	cache = Cache()
	removed_count = 0
	removed_size = 0

	for deb_file in ARCHIVE_DIR.glob("*.deb"):
		pkg_name_part = deb_file.name.split("_")[0]
		should_remove = True

		# Keep the file if it corresponds to the current candidate version
		if pkg_name_part in cache:
			pkg = cache[pkg_name_part]
			if pkg.candidate:
				candidate_filename = get_pkg_name(pkg.candidate)
				if deb_file.name == candidate_filename:
					should_remove = False

		if should_remove:
			try:
				size = deb_file.stat().st_size
				deb_file.unlink()
				removed_count += 1
				removed_size += size
				vprint(_("Removed: {filename}").format(filename=deb_file))
			except OSError as err:
				eprint(f"{ERROR_PREFIX} {err}")

	if removed_count:
		print(
			_("Autoclean removed {count} file(s), freeing {size}.").format(
				count=color(str(removed_count), "GREEN"),
				size=unit_str(removed_size).strip(),
			)
		)
	else:
		print(_("Autoclean found nothing to remove."))


# ── check ────────────────────────────────────────────────────────────────────

@nala.command(help=_("Verify there are no broken dependencies."))
def check(
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Check the system for any broken package dependencies.

	Equivalent to 'apt-get check'. Exits with status 1 if broken packages
	are found.
	"""
	cache = Cache()
	broken: list[str] = []

	for pkg in cache:
		if not pkg.installed or not pkg.installed.dependencies:
			continue
		for dep_group in pkg.installed.dependencies:
			if dep_group.installed_target_versions:
				continue
			# The dep is unmet; check if it's a known virtual package or exists at all
			if any(
				cache.is_virtual_package(bd.name) or bd.name in cache
				for bd in dep_group
			):
				continue
			broken.append(
				f"  {color(pkg.name, 'GREEN')}: "
				f"missing dependency {color(dep_group[0].name, 'YELLOW')}"
			)

	if cache.broken_count or broken:
		eprint(
			_("{error} Found {count} broken package(s):").format(
				error=ERROR_PREFIX,
				count=color(str(cache.broken_count or len(broken)), "RED"),
			)
		)
		for line in broken:
			eprint(line)
		sys.exit(1)

	print(color(_("No broken dependencies found."), "GREEN"))


# ── depends ──────────────────────────────────────────────────────────────────

@nala.command(help=_("Show dependency information for packages."))
def depends(
	pkg_names: List[str] = typer.Argument(
		...,
		help=_("Package(s) to show dependencies for"),
		autocompletion=package_completion,
	),
	installed: bool = typer.Option(
		False,
		"-i",
		"--installed",
		help=_("Show dependencies for the installed version."),
	),
	recurse: bool = typer.Option(
		False,
		"--recurse",
		help=_("Recurse into dependencies of dependencies."),
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Show dependency information for packages.

	Equivalent to 'apt-cache depends'. Use --recurse for a full dependency
	tree and --installed to examine the installed version specifically.
	"""
	cache = Cache()
	not_found: list[str] = []
	seen: set[str] = set()

	def _show_deps(pkg_name: str, indent: int = 0) -> None:
		"""Recursively display dependencies."""
		prefix = "  " * indent
		if pkg_name not in cache:
			eprint(
				_("{error} {package} not found").format(
					error=ERROR_PREFIX, package=color(pkg_name, "YELLOW")
				)
			)
			return

		pkg = cache[pkg_name]
		version = pkg.installed if (installed and pkg.installed) else pkg.candidate
		if not version:
			eprint(
				_("{error} {package} has no candidate version").format(
					error=ERROR_PREFIX, package=color(pkg_name, "YELLOW")
				)
			)
			return

		if indent == 0:
			print(color(pkg_name, "GREEN"))

		for dep_group in version.dependencies:
			for dep in dep_group:
				dep_str = color(dep.name, "GREEN")
				if dep.version:
					dep_str += f" ({color(dep.relation)} {color(dep.version, 'BLUE')})"
				print(f"{prefix}  {dep_str}")

				if recurse and dep.name not in seen:
					seen.add(dep.name)
					_show_deps(dep.name, indent + 2)

	for pkg_name in pkg_names:
		if pkg_name not in cache:
			not_found.append(pkg_name)
			continue
		_show_deps(pkg_name)

	if not_found:
		pkg_error(not_found, cache)


# ── rdepends ─────────────────────────────────────────────────────────────────

@nala.command(help=_("Show reverse dependency information for packages."))
def rdepends(
	pkg_names: List[str] = typer.Argument(
		...,
		help=_("Package(s) to show reverse dependencies for"),
		autocompletion=package_completion,
	),
	installed: bool = typer.Option(
		False,
		"-i",
		"--installed",
		help=_("Show only installed reverse dependencies."),
	),
	recurse: bool = typer.Option(
		False,
		"--recurse",
		help=_("Recurse into reverse dependencies."),
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Show reverse dependency information for packages.

	Equivalent to 'apt-cache rdepends'. Lists all packages that depend on
	the specified package(s).
	"""
	cache = Cache()
	not_found: list[str] = []
	seen: set[str] = set()

	def _show_rdeps(pkg_name: str, indent: int = 0) -> None:
		"""Recursively display reverse dependencies."""
		prefix = "  " * indent
		if indent == 0:
			print(color(pkg_name, "GREEN"))
			print(f"{prefix}{color(_('Reverse Depends:'), 'CYAN')}")

		for pkg in cache:
			check_version = pkg.installed if installed else pkg.candidate
			if not check_version:
				continue
			for dep_group in check_version.dependencies:
				for dep in dep_group:
					if dep.name == pkg_name:
						print(f"{prefix}  {color(pkg.name, 'GREEN')}")
						if recurse and pkg.name not in seen:
							seen.add(pkg.name)
							_show_rdeps(pkg.name, indent + 2)
						break

	for pkg_name in pkg_names:
		if pkg_name not in cache:
			not_found.append(pkg_name)
			continue
		_show_rdeps(pkg_name)

	if not_found:
		pkg_error(not_found, cache)


# ── policy ───────────────────────────────────────────────────────────────────

@nala.command(help=_("Show policy and priority settings for packages."))
def policy(
	pkg_names: Optional[List[str]] = typer.Argument(
		None,
		help=_("Package(s) to show policy for. Shows all source priorities if none given."),
		autocompletion=package_completion,
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Show policy settings and version priorities for packages.

	Equivalent to 'apt-cache policy'. Without arguments, shows the priority
	assigned to every configured package source. With package arguments, shows
	which version of each package would be installed and why.
	"""
	cache = Cache()
	raw_cache = cache._cache
	apt_policy = apt_pkg.Policy(raw_cache)
	apt_policy.init_defaults()

	if not pkg_names:
		# Global view: show all configured source priorities
		print(color(_("Package source priorities:"), "CYAN"))
		seen_sites: set[str] = set()
		for pkg in raw_cache.packages:  # pylint: disable=not-an-iterable
			for ver in pkg.version_list:
				for pf, _idx in ver.file_list:
					site_key = f"{pf.site}:{pf.archive}:{pf.component}"
					if site_key in seen_sites:
						continue
					seen_sites.add(site_key)
					priority = apt_policy.get_priority(pf)
					label = pf.site or pf.label or pf.archive or "(local)"
					archive = pf.archive or pf.component or "now"
					print(
						f"  {color(str(priority), 'BLUE')}"
						f" {label} {archive}/{pf.component or 'dpkg'}"
					)
		return

	not_found: list[str] = []
	for pkg_name in pkg_names:
		if pkg_name not in cache:
			not_found.append(pkg_name)
			continue

		pkg = cache[pkg_name]
		raw_pkg = raw_cache[pkg_name]

		installed_ver = pkg.installed.version if pkg.installed else _("(none)")
		candidate_ver = pkg.candidate.version if pkg.candidate else _("(none)")

		print(f"{color(pkg_name, 'GREEN')}:")
		print(
			f"  {color(_('Installed:'))} "
			f"{color(installed_ver, 'BLUE') if pkg.installed else installed_ver}"
		)
		print(
			f"  {color(_('Candidate:'))} "
			f"{color(candidate_ver, 'BLUE') if pkg.candidate else candidate_ver}"
		)
		print(f"  {color(_('Version table:'))}")

		for ver in raw_pkg.version_list:
			is_installed = bool(pkg.installed and pkg.installed.version == ver.ver_str)
			marker = "***" if is_installed else "   "
			print(f" {color(marker, 'GREEN')} {color(ver.ver_str, 'BLUE')}")

			for pf, _idx in ver.file_list:
				priority = apt_policy.get_priority(pf)
				if pf.archive == "now":
					print(f"        {color(str(priority), 'BLUE')} /var/lib/dpkg/status")
				else:
					print(
						f"        {color(str(priority), 'BLUE')} "
						f"http://{pf.site} {pf.archive}/{pf.component} "
						f"{pf.arch or 'amd64'} Packages"
					)

	if not_found:
		pkg_error(not_found, cache)


# ── download ─────────────────────────────────────────────────────────────────

@nala.command(help=_("Download packages to the current directory."))
def download(
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to download"),
		autocompletion=package_completion,
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Download binary packages into the current directory.

	Equivalent to 'apt-get download'. Does not install packages, only fetches
	the .deb files into the current working directory.
	"""
	cache = Cache()
	not_found: list[str] = []

	for pkg_name in pkg_names:
		if pkg_name not in cache:
			not_found.append(pkg_name)
			continue

		pkg = cache[pkg_name]
		if not pkg.candidate:
			eprint(
				_("{error} {package} has no installation candidate").format(
					error=ERROR_PREFIX, package=color(pkg_name, "YELLOW")
				)
			)
			sys.exit(1)

		cand = pkg.candidate
		print(
			_("Downloading {package} {version}").format(
				package=color(pkg_name, "GREEN"),
				version=color(cand.version, "BLUE"),
			)
		)

		# Delegate to apt-get download: it handles authentication, mirrors, and
		# partial-download resumption correctly without reimplementing all that logic.
		result = run(
			["apt-get", "download", pkg_name],
			check=False,
			cwd=str(Path.cwd()),
		)
		if result.returncode != 0:
			eprint(
				_("{error} Failed to download {package}").format(
					error=ERROR_PREFIX, package=color(pkg_name, "YELLOW")
				)
			)
			sys.exit(result.returncode)

	if not_found:
		pkg_error(not_found, cache)


# ── build-dep ────────────────────────────────────────────────────────────────

@nala.command("build-dep", help=_("Install build dependencies for source packages."))
def build_dep(
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to install build dependencies for"),
		autocompletion=package_completion,
	),
	assume_yes: bool = ASSUME_YES,
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Install all packages needed to build the specified source package(s).

	Equivalent to 'apt-get build-dep'. Requires root.
	"""
	sudo_check()
	cmd = ["apt-get", "build-dep"]
	if arguments.assume_yes:
		cmd.append("-y")
	cmd.extend(pkg_names)
	result = run(cmd, check=False)
	sys.exit(result.returncode)


# ── satisfy ──────────────────────────────────────────────────────────────────

@nala.command(help=_("Satisfy arbitrary dependency strings."))
def satisfy(
	dep_strings: List[str] = typer.Argument(
		...,
		metavar="DEPS ...",
		help=_("Dependency string(s) to satisfy, e.g. 'python3 (>= 3.8)'"),
	),
	assume_yes: bool = ASSUME_YES,
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Ensure that the given dependency expressions are satisfied.

	Equivalent to 'apt-get satisfy'. Accepts standard Debian dependency
	syntax such as 'python3 (>= 3.8), git'. Requires root.
	"""
	sudo_check()
	cmd = ["apt-get", "satisfy"]
	if arguments.assume_yes:
		cmd.append("-y")
	cmd.extend(dep_strings)
	result = run(cmd, check=False)
	sys.exit(result.returncode)


# ── source ───────────────────────────────────────────────────────────────────

@nala.command(help=_("Download source archives."))
def source(
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to download source for"),
		autocompletion=package_completion,
	),
	download_only: bool = typer.Option(
		False,
		"-d",
		"--download-only",
		help=_("Only download; do not unpack or build."),
	),
	build: bool = typer.Option(
		False,
		"-b",
		"--build",
		help=_("Build the source package after downloading."),
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Download source packages into the current directory.

	Equivalent to 'apt-get source'. deb-src lines must be present in
	sources.list for this to work. Use --build to compile the package after
	downloading, or --download-only to skip unpacking.
	"""
	cmd = ["apt-get", "source"]
	if download_only:
		cmd.append("-d")
	if build:
		cmd.append("-b")
	cmd.extend(pkg_names)
	result = run(cmd, check=False)
	sys.exit(result.returncode)


# ── changelog ────────────────────────────────────────────────────────────────

@nala.command(help=_("Fetch and display the changelog for packages."))
def changelog(
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to show changelog for"),
		autocompletion=package_completion,
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Download and display the changelog for packages.

	Equivalent to 'apt-get changelog'. Fetches changelogs from the
	configured sources and pages through them.
	"""
	cmd = ["apt-get", "changelog", *pkg_names]
	result = run(cmd, check=False)
	sys.exit(result.returncode)


# ── edit-sources ─────────────────────────────────────────────────────────────

@nala.command("edit-sources", help=_("Edit the sources.list file."))
def edit_sources(
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Open the sources.list file in an editor with sanity checking.

	Equivalent to 'apt edit-sources'. Uses $EDITOR or a sensible fallback.
	Requires root.
	"""
	sudo_check()
	result = run(["apt", "edit-sources"], check=False)
	sys.exit(result.returncode)


# ── hold / unhold / showhold (apt-mark equivalents) ──────────────────────────

@nala.command(help=_("Hold packages at their current version."))
def hold(
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to hold"),
		autocompletion=package_completion,
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Mark packages as held back, preventing automatic upgrades.

	Equivalent to 'apt-mark hold'. Held packages are excluded from
	'nala upgrade' and 'nala full-upgrade'.
	"""
	sudo_check()
	cache = Cache()
	not_found: list[str] = []

	for pkg_name in pkg_names:
		if pkg_name not in cache:
			not_found.append(pkg_name)

	if not_found:
		pkg_error(not_found, cache)

	result = run(["apt-mark", "hold", *pkg_names], check=False)
	sys.exit(result.returncode)


@nala.command(help=_("Remove a hold on packages to allow upgrades."))
def unhold(
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to unhold"),
		autocompletion=package_completion,
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Remove the held-back mark from packages, re-enabling automatic upgrades.

	Equivalent to 'apt-mark unhold'.
	"""
	sudo_check()
	result = run(["apt-mark", "unhold", *pkg_names], check=False)
	sys.exit(result.returncode)


@nala.command(help=_("Show packages that are held back."))
def showhold(
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Print a list of all packages currently on hold.

	Equivalent to 'apt-mark showhold'.
	"""
	result = run(
		["apt-mark", "showhold"], capture_output=True, text=True, check=False
	)
	if result.returncode != 0:
		eprint(result.stderr)
		sys.exit(result.returncode)

	held = result.stdout.strip().splitlines()
	if not held:
		print(_("No packages are held back."))
		return
	print(color(_("Held packages:"), "CYAN"))
	for pkg_name in sorted(held):
		print(f"  {color(pkg_name, 'GREEN')}")


# ── mark / showauto / showmanual / minimize-manual (apt-mark) ────────────────

@nala.command("mark", help=_("Mark packages as automatically or manually installed."))
def mark(
	state: str = typer.Argument(
		...,
		metavar="auto|manual",
		help=_("Installation state to assign: 'auto' or 'manual'"),
	),
	pkg_names: List[str] = typer.Argument(
		...,
		metavar="PKGS ...",
		help=_("Package(s) to mark"),
		autocompletion=package_completion,
	),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Mark packages as automatically or manually installed.

	Equivalent to 'apt-mark auto' / 'apt-mark manual'. Packages marked as
	'auto' are eligible for removal by 'nala autoremove' when nothing depends
	on them. Packages marked as 'manual' are protected from auto-removal.
	"""
	sudo_check()
	if state not in ("auto", "manual"):
		eprint(
			_("{error} State must be 'auto' or 'manual', not '{state}'").format(
				error=ERROR_PREFIX, state=color(state, "YELLOW")
			)
		)
		sys.exit(1)

	cache = Cache()
	not_found: list[str] = []
	for pkg_name in pkg_names:
		if pkg_name not in cache:
			not_found.append(pkg_name)

	if not_found:
		pkg_error(not_found, cache)

	result = run(["apt-mark", state, *pkg_names], check=False)
	sys.exit(result.returncode)


@nala.command("showauto", help=_("Show automatically installed packages."))
def showauto(
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Print a list of all automatically installed packages.

	Equivalent to 'apt-mark showauto'. These packages were pulled in as
	dependencies and may be removed by 'nala autoremove'.
	"""
	result = run(
		["apt-mark", "showauto"], capture_output=True, text=True, check=False
	)
	if result.returncode != 0:
		eprint(result.stderr)
		sys.exit(result.returncode)

	pkgs = result.stdout.strip().splitlines()
	if not pkgs:
		print(_("No packages marked as automatically installed."))
		return
	print(color(_("Automatically installed packages:"), "CYAN"))
	for pkg_name in sorted(pkgs):
		print(f"  {color(pkg_name, 'GREEN')}")


@nala.command("showmanual", help=_("Show manually installed packages."))
def showmanual(
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Print a list of all manually installed packages.

	Equivalent to 'apt-mark showmanual'. These packages were explicitly
	installed by the user and will not be auto-removed.
	"""
	result = run(
		["apt-mark", "showmanual"], capture_output=True, text=True, check=False
	)
	if result.returncode != 0:
		eprint(result.stderr)
		sys.exit(result.returncode)

	pkgs = result.stdout.strip().splitlines()
	if not pkgs:
		print(_("No packages marked as manually installed."))
		return
	print(color(_("Manually installed packages:"), "CYAN"))
	for pkg_name in sorted(pkgs):
		print(f"  {color(pkg_name, 'GREEN')}")


@nala.command(
	"minimize-manual",
	help=_("Mark dependencies of meta-packages as automatically installed."),
)
def minimize_manual(
	assume_yes: bool = ASSUME_YES,
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	man_help: bool = MAN_HELP,
	color_force: bool = COLOR,
) -> None:
	"""Mark all dependencies of meta-packages as automatically installed.

	Equivalent to 'apt-mark minimize-manual'. Useful after installing a
	task or meta-package: marks all pulled-in dependencies as 'auto' so
	they can be cleaned up if the meta-package is later removed.
	"""
	sudo_check()
	cmd = ["apt-mark", "minimize-manual"]
	if arguments.assume_yes:
		cmd.append("-y")
	result = run(cmd, check=False)
	sys.exit(result.returncode)
