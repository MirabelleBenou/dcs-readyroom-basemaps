"""Les mentions (règle 12), en anglais, au caractère près : un paragraphe par source présente au manifeste."""

OSM_LONG = ("Map data © OpenStreetMap contributors, available under the Open Database License (ODbL) v1.0, "
            "https://www.openstreetmap.org/copyright. The vector tiles in fond.pmtiles are a Produced Work derived "
            "from this data and are made available under the ODbL.")
OSM_COURT = "© OpenStreetMap contributors (ODbL)"


def _copernicus_long(n):
    return ("Relief produced using Copernicus WorldDEM-%d © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
            "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The "
            "organisations in charge of the Copernicus programme by law or by delegation do not incur any "
            "liability for any use of the Copernicus WorldDEM-%d." % (n, n))


GLO30_LONG = _copernicus_long(30)
GLO30_COURT = "Copernicus WorldDEM-30 © DLR e.V., © Airbus DS"
GLO90_LONG = _copernicus_long(90)
GLO90_COURT = "Copernicus WorldDEM-90 © DLR e.V., © Airbus DS"
# Special Elite : le détenteur de METADATA.pb et de la table `name` de la police amont (google/fonts, apache/specialelite) ;
# JetBrains Mono : celui de l'en-tête de sa licence OFL.
POLICES = ("Special Elite © Brian J. Bonislawsky DBA Astigmatic (AOETI), Apache License 2.0; "
           "JetBrains Mono © The JetBrains Mono Project Authors, SIL Open Font License 1.1")
CODE = "MIT License"
CODE_DETENTEUR = "Copyright (c) 2026 MirabelleBenou"


def sources_relief(entrees_relief):
    """{"glo30", "glo90"} : les sources de relief réellement utilisées (une dalle absente n'en est pas une)."""
    return {e["source"] for e in entrees_relief if e.get("source") in ("glo30", "glo90")}


def paragraphes(entrees_relief):
    """Les paragraphes de LICENCE.txt, dans l'ordre ; une source absente du manifeste n'a pas de paragraphe."""
    src = sources_relief(entrees_relief)
    p = [OSM_LONG]
    if "glo30" in src:
        p.append(GLO30_LONG)
    if "glo90" in src:
        p.append(GLO90_LONG)
    p += [POLICES, CODE, CODE_DETENTEUR]
    return p


def texte(entrees_relief):
    return "\n\n".join(paragraphes(entrees_relief)) + "\n"


def attributions_courtes(entrees_relief):
    """Les attributions des sources du style : {"fond": ..., "relief": ...}."""
    src = sources_relief(entrees_relief)
    rel = []
    if "glo30" in src:
        rel.append(GLO30_COURT)
    if "glo90" in src:
        rel.append(GLO90_COURT)
    return {"fond": OSM_COURT, "relief": ", ".join(rel)}
