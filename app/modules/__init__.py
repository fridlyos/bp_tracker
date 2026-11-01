"""Module registry.

A module is a self-contained feature (its own pages under /<key>, JSON API
under /api/<key>, templates under templates/<key>/, service and schemas). The
launcher at / shows one tile per module; inside a module the tab bar shows that
module's pages.

To add a module: create app/modules/<key>/ exposing MODULE (see bp/), then
append it to MODULES below. Tables go in a new migration as usual.
"""
from dataclasses import dataclass, field
from typing import Callable

from fastapi import APIRouter


@dataclass(frozen=True)
class NavItem:
    href: str
    label: str
    icon: str  # inner SVG markup for a 24x24 stroked icon (trusted, static)


@dataclass(frozen=True)
class Module:
    key: str
    name: str
    description: str
    icon: str                 # inner SVG markup, shown on the launcher tile
    nav: tuple[NavItem, ...]  # first item is the module's start page
    routers: tuple[APIRouter, ...] = ()
    # optional one-line status for the launcher tile, e.g. "Last: 128/82 · today"
    summary: Callable | None = field(default=None, compare=False)

    @property
    def href(self) -> str:
        return self.nav[0].href

    def active(self, path: str) -> NavItem | None:
        """The nav item for `path` (longest matching prefix)."""
        hits = [n for n in self.nav if path == n.href or path.startswith(n.href.rstrip("/") + "/")]
        return max(hits, key=lambda n: len(n.href), default=None)


from .bp import MODULE as BP  # noqa: E402

MODULES: tuple[Module, ...] = (BP,)
