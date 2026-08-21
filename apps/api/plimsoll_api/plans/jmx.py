"""A .jmx is executable configuration supplied by a user: untrusted input.

defusedxml refuses external entities and the billion-laughs expansion, both of
which the stock parser resolves happily inside the API container.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import ParseError, fromstring

# ${NAME}, excluding ${__function(...)} which JMeter resolves itself.
VARIABLE = re.compile(r"\$\{(?!__)([A-Za-z_][A-Za-z0-9_]*)\}")

DOMAIN_PROP = "HTTPSampler.domain"
PORT_PROP = "HTTPSampler.port"
PROTOCOL_PROP = "HTTPSampler.protocol"


class PlanParseError(Exception):
    """The document is not well-formed, or resolves entities we refuse."""


@dataclass(frozen=True)
class PlanTarget:
    scheme: str
    host: str
    port: int | None


@dataclass(frozen=True)
class PlanSummary:
    thread_groups: list[str] = field(default_factory=list)
    transaction_controllers: list[str] = field(default_factory=list)
    timers: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    data_files: list[str] = field(default_factory=list)
    targets: list[PlanTarget] = field(default_factory=list)


def _name(element: Element) -> str:
    return element.get("testname", "").strip()


def _string_props(element: Element) -> dict[str, str]:
    return {
        prop.get("name", ""): (prop.text or "").strip() for prop in element.findall("stringProp")
    }


def _port(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def parse_plan(text: str) -> PlanSummary:
    try:
        root = fromstring(text)
    except (ParseError, ValueError) as exc:
        raise PlanParseError(str(exc)) from exc

    thread_groups: list[str] = []
    controllers: list[str] = []
    timers: list[str] = []
    data_files: list[str] = []
    targets: list[PlanTarget] = []
    defaults: dict[str, str] = {}

    for element in root.iter():
        testclass = element.get("testclass", "")
        if testclass.endswith("ThreadGroup"):
            thread_groups.append(_name(element))
        elif testclass == "TransactionController":
            controllers.append(_name(element))
        elif testclass.endswith("Timer"):
            timers.append(_name(element))
        elif testclass == "CSVDataSet":
            filename = _string_props(element).get("filename", "")
            if filename:
                data_files.append(filename)
        elif testclass == "ConfigTestElement":
            props = _string_props(element)
            if props.get(DOMAIN_PROP):
                defaults = props
        elif testclass == "HTTPSamplerProxy":
            props = _string_props(element)
            host = props.get(DOMAIN_PROP) or defaults.get(DOMAIN_PROP, "")
            if not host:
                continue
            targets.append(
                PlanTarget(
                    scheme=props.get(PROTOCOL_PROP) or defaults.get(PROTOCOL_PROP) or "http",
                    host=host,
                    port=_port(props.get(PORT_PROP) or defaults.get(PORT_PROP) or ""),
                )
            )

    if defaults.get(DOMAIN_PROP):
        targets.append(
            PlanTarget(
                scheme=defaults.get(PROTOCOL_PROP) or "http",
                host=defaults[DOMAIN_PROP],
                port=_port(defaults.get(PORT_PROP, "")),
            )
        )

    return PlanSummary(
        thread_groups=sorted({name for name in thread_groups if name}),
        transaction_controllers=sorted({name for name in controllers if name}),
        timers=sorted({name for name in timers if name}),
        # Collected from the document text rather than per element: a reference
        # can hide in an attribute, a header value, a path, or a body, and
        # enumerating the places it may appear is how one gets missed.
        variables=sorted(set(VARIABLE.findall(text))),
        data_files=sorted(set(data_files)),
        targets=sorted(set(targets), key=lambda target: (target.host, target.port or 0)),
    )
