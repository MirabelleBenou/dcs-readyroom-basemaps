from fabrique import licence


def test_un_paragraphe_par_source_presente():
    r30 = [{"source": "glo30"}, {"source": "absente"}]
    p = licence.paragraphes(r30)
    assert licence.OSM_LONG in p and licence.GLO30_LONG in p and licence.POLICES in p and licence.CODE in p
    assert licence.GLO90_LONG not in p
    r90 = [{"source": "glo30"}, {"source": "glo90"}]
    p = licence.paragraphes(r90)
    assert licence.GLO30_LONG in p and licence.GLO90_LONG in p
    assert "WorldDEM-90" in licence.GLO90_LONG and "WorldDEM-30" not in licence.GLO90_LONG
    # rien pour une source absente du manifeste : une mer pure n'a pas de paragraphe Copernicus
    p = licence.paragraphes([{"source": "absente"}])
    assert licence.GLO30_LONG not in p and licence.GLO90_LONG not in p


def test_textes_au_caractere_pres():
    assert licence.OSM_LONG == ("Map data © OpenStreetMap contributors, available under the Open Database License (ODbL) "
                                "v1.0, https://www.openstreetmap.org/copyright. The vector tiles in fond.pmtiles are a "
                                "Produced Work derived from this data and are made available under the ODbL.")
    assert licence.GLO30_COURT == "Copernicus WorldDEM-30 © DLR e.V., © Airbus DS"
    assert licence.OSM_COURT == "© OpenStreetMap contributors (ODbL)"
    assert licence.POLICES == ("Special Elite © Brian J. Bonislawsky DBA Astigmatic (AOETI), Apache License 2.0; "
                               "JetBrains Mono © The JetBrains Mono Project Authors, SIL Open Font License 1.1")
    assert licence.GLO30_LONG == ("Relief produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence "
                                  "and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all "
                                  "rights reserved. The organisations in charge of the Copernicus programme by law or by "
                                  "delegation do not incur any liability for any use of the Copernicus WorldDEM-30.")
    assert licence.GLO90_LONG == ("Relief produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and © Airbus Defence "
                                  "and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all "
                                  "rights reserved. The organisations in charge of the Copernicus programme by law or by "
                                  "delegation do not incur any liability for any use of the Copernicus WorldDEM-90.")
    assert licence.GLO90_COURT == "Copernicus WorldDEM-90 © DLR e.V., © Airbus DS"
    assert licence.CODE == "MIT License" and licence.CODE_DETENTEUR == "Copyright (c) 2026 MirabelleBenou"


def test_attributions_courtes_par_source():
    a = licence.attributions_courtes([{"source": "glo30"}])
    assert a == {"fond": licence.OSM_COURT, "relief": licence.GLO30_COURT}
    a = licence.attributions_courtes([{"source": "glo30"}, {"source": "glo90"}])
    assert licence.GLO90_COURT in a["relief"] and licence.GLO30_COURT in a["relief"]
