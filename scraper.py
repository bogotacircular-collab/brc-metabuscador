# -*- coding: utf-8 -*-
"""
scraper.py - BRC Metabuscador v3
Fuentes:
  1. BazzarBog CCB           - HTML estatico (PrestaShop)
  2. Negocios Verdes Colombia - API SODA datos.gov.co (filtro CAR + Bogota)
  3. ECMarketplace Latam      - Playwright (JS rendering)
  4. Ecodirectorio SDA        - Playwright (Google Sites)
"""

import json
import re
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

OUTPUT_FILE     = Path(__file__).parent / "data.json"
LOG_FORMAT      = "%(asctime)s [%(levelname)s] %(message)s"
REQUEST_TIMEOUT = 25
TODAY           = datetime.now(timezone.utc).isoformat()

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("brc-scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BRC-Metabuscador/2.0; "
        "+https://bogotacircular-collab.github.io/brc-metabuscador)"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

# CLASIFICADOR

TIPO_KW = {
    "financiacion": [
        "fondo", "credito", "financiacion", "convocatoria", "beca",
        "inversion", "capital", "linea verde", "prestamo"
    ],
    "tecnologia": [
        "plataforma", "sistema", "software", "blockchain", "trazabilidad",
        "sensor", "iot", "ia", "equipo", "maquinaria", "trituracion",
        "tecnologia", "app", "digital"
    ],
    "capacidad": [
        "laboratorio", "investigacion", "i+d", "universidad", "centro",
        "ensayo", "certificacion", "acreditacion", "capacidad instalada",
        "caracterizacion"
    ],
    "servicio": [
        "recoleccion", "gestion", "logistica", "consultoria", "asesoria",
        "mantenimiento", "distribucion", "servicio", "implementacion",
        "lavado", "reciclaje", "instalacion"
    ],
    "bien": [
        "material", "materia prima", "pellet", "compost", "residuo",
        "producto", "arido", "fibra", "insumo", "bien", "venta", "compra"
    ],
}

SECTOR_KW = {
    "agua": [
        "agua", "acuifero", "tratamiento", "riego", "vertimiento",
        "hidrologico", "efluente", "humedal", "cuenca", "hidrico"
    ],
    "agro": [
        "agro", "alimento", "organico", "compost", "biorresiduos",
        "agricola", "alimentario", "humus", "lombriz", "ganadero",
        "cafe", "cacao", "hortalizas", "frutas", "verduras"
    ],
    "textil": [
        "textil", "fibra", "ropa", "moda", "upcycling", "confeccion",
        "tela", "hilo", "vestimenta", "cuero", "calzado"
    ],
    "construccion": [
        "construccion", "demolicion", "rcd", "arido", "concreto",
        "cemento", "obra", "edificio", "infraestructura", "yeso",
        "madera", "bambu"
    ],
    "residuos": [
        "residuo", "reciclaje", "plastico", "papel", "carton",
        "electronico", "respel", "basura", "aprovechamiento",
        "punto ecologico", "reciclado", "caneca"
    ],
}

ESTRATEGIA_KW = {
    "Diseno circular": [
        "diseno", "ecodiseno", "trazabilidad", "ciclo de vida",
        "blockchain", "sostenible", "ecologico", "verde"
    ],
    "Extension vida util": [
        "reparacion", "reutilizacion", "segunda vida", "refabricacion",
        "mantenimiento", "extension vida", "upcycling"
    ],
    "Valorizacion y cierre": [
        "reciclaje", "valorizacion", "compostaje", "biogas", "pellet",
        "recuperacion", "cierre de ciclo", "recoleccion", "aprovechamiento"
    ],
    "Modelos de negocio": [
        "plataforma", "marketplace", "startup", "escala", "fondo",
        "inversion", "capital", "financiacion", "vitrina", "emprendimiento"
    ],
    "Simbiosis industrial": [
        "simbiosis", "industrial", "subproducto",
        "intercambio de materiales", "residuo de uno"
    ],
}


def _score(text, kw_dict):
    tl = text.lower()
    scores = {k: sum(1 for w in v if w in tl) for k, v in kw_dict.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def classify(title="", desc=""):
    combined = f"{title} {desc}"
    return {
        "tipo":       _score(combined, TIPO_KW)       or "servicio",
        "sector":     _score(combined, SECTOR_KW)     or "residuos",
        "estrategia": _score(combined, ESTRATEGIA_KW) or "Valorizacion y cierre",
    }


def make_record(title, actor, source, desc,
                loc="Bogota D.C.", anno=None, url="", detail="", **extra):
    cl = classify(title, desc)
    return {
        **cl,
        **extra,
        "title":      title.strip()[:220],
        "actor":      actor.strip()[:120],
        "source":     source,
        "url":        url,
        "desc":       desc.strip()[:240],
        "detail":     detail.strip()[:600],
        "loc":        loc,
        "ano":        str(anno or datetime.now().year),
        "scraped_at": TODAY,
    }


# FUENTE 1 - BAZZARBOG

BAZZARBOG_KEYWORDS = [
    "reciclado", "sostenible", "ecologico", "organico",
    "biodegradable", "economia circular", "upcycling", "bambu",
    "compostable", "reutilizable", "bioplastico", "verde",
]


def scrape_bazzarbog(client):
    SOURCE    = "BazzarBog CCB"
    BASE      = "https://www.bazzarbog.com"
    seen_urls = set()
    results   = []

    for kw in BAZZARBOG_KEYWORDS:
        try:
            url = f"{BASE}/busqueda?s={kw}"
            log.info(f"[BazzarBog] Buscando: '{kw}'")
            res = client.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")
            cards = soup.select(
                "article.product-miniature, .product-miniature, .js-product"
            )
            for card in cards:
                link_el  = card.select_one(
                    "a.product-thumbnail, h3 a, h2 a, .product-title a"
                )
                title_el = card.select_one(".product-title, h3, h2")
                price_el = card.select_one(".price, .product-price")
                brand_el = card.select_one(
                    ".product-manufacturer, .brand, .manufacturer"
                )
                if not link_el:
                    continue
                product_url = link_el.get("href", "")
                if not product_url.startswith("http"):
                    product_url = BASE + product_url
                if product_url in seen_urls:
                    continue
                seen_urls.add(product_url)
                title = (
                    title_el.get_text(strip=True) if title_el
                    else link_el.get_text(strip=True) or "Producto sostenible"
                )
                actor = (
                    brand_el.get_text(strip=True) if brand_el
                    else "BazzarBog CCB"
                )
                price = price_el.get_text(strip=True) if price_el else ""
                detail_text = ""
                if len(results) < 60:
                    detail_text = _bazzarbog_detail(client, product_url)
                desc = (
                    detail_text[:240] if detail_text
                    else f"Producto sostenible: {title}. {price}"
                )
                results.append(make_record(
                    title=title, actor=actor, source=SOURCE,
                    desc=desc, detail=detail_text, url=product_url,
                ))
        except Exception as e:
            log.warning(f"[BazzarBog] Error keyword '{kw}': {e}")

    log.info(f"[BazzarBog] {len(results)} productos extraidos")
    return results


def _bazzarbog_detail(client, url):
    try:
        res  = client.get(url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(res.text, "lxml")
        el   = (
            soup.select_one(".product-description-short")
            or soup.select_one("#product-description-short")
            or soup.select_one(".product-description")
            or soup.select_one('[itemprop="description"]')
        )
        if el:
            return " ".join(el.get_text(separator=" ", strip=True).split())[:600]
    except Exception:
        pass
    return ""


# FUENTE 2 - NEGOCIOS VERDES API SODA
# Dataset: https://www.datos.gov.co/resource/v29b-znjj.json
# Filtro: autoridad_ambiental_donde = CAR (Cundinamarca)
#         O departamento_donde_se = Bogota (Distrito)

def scrape_negocios_verdes_api(client):
    SOURCE   = "Negocios Verdes - CAR / Bogota"
    BASE_API = "https://www.datos.gov.co/resource/v29b-znjj.json"
    results  = []

    # Campos reales confirmados del dataset:
    # departamento_donde_se, municipio_donde_se_encuentra,
    # autoridad_ambiental_donde, raz_n_social_del_negocio,
    # descripci_n_del_negocio_verde, categor_a_del_negocio_verde,
    # sector_al_cual_pertenece, producto_principal_que, a_o_a_o_de_registro

    params = {
        "$limit":  1000,
        "$offset": 0,
        "$order":  "raz_n_social_del_negocio ASC",
        "$where": (
            "autoridad_ambiental_donde = 'CAR' "
            "OR departamento_donde_se = 'Bogota' "
            "OR departamento_donde_se = 'Cundinamarca'"
        ),
    }

    try:
        log.info("[NegociosVerdes] Consultando API SODA datos.gov.co...")
        res = client.get(BASE_API, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        log.info(f"[NegociosVerdes] {len(data)} registros recibidos del API")

        for item in data:
            nombre    = (item.get("raz_n_social_del_negocio") or "Sin nombre").strip()
            desc_raw  = (item.get("descripci_n_del_negocio_verde") or "").strip()
            categoria = (item.get("categor_a_del_negocio_verde") or "").strip()
            sector    = (item.get("sector_al_cual_pertenece") or "").strip()
            subsector = (item.get("subsector_al_cual_pertenece") or "").strip()
            producto  = (item.get("producto_principal_que") or "").strip()
            municipio = (item.get("municipio_donde_se_encuentra") or "").strip()
            dpto      = (item.get("departamento_donde_se") or "").strip()
            autoridad = (item.get("autoridad_ambiental_donde") or "").strip()
            anno      = (item.get("a_o_a_o_de_registro") or "").strip()
            rep       = (item.get("nombre_representante_del") or "").strip()

            if not nombre or nombre == "Sin nombre":
                continue

            loc = f"{municipio}, {dpto}".strip(", ") or "Bogota Region"

            desc = desc_raw[:240] if desc_raw else (
                f"Negocio verde verificado por {autoridad}. "
                f"Categoria: {categoria}. Producto: {producto}."
            )

            detail = (
                f"{desc_raw} "
                f"Sector: {sector}. Subsector: {subsector}. "
                f"Producto principal: {producto}. "
                f"Autoridad ambiental: {autoridad}. "
                f"Representante: {rep}."
            ).strip()

            results.append(make_record(
                title=nombre,
                actor=nombre,
                source=SOURCE,
                desc=desc,
                detail=detail,
                loc=loc,
                anno=anno or None,
                url="https://www.negociosverdes.gov.co",
            ))

    except Exception as e:
        log.error(f"[NegociosVerdes] Error: {e}")

    log.info(f"[NegociosVerdes] {len(results)} registros procesados")
    return results


# FUENTE 3 + 4 - ECMARKETPLACE y ECODIRECTORIO SDA (Playwright)

def scrape_con_playwright():
    results_ecmarket = []
    results_ecdir    = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("[Playwright] No instalado. Skipping.")
        return [], []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )

            # ECMarketplace
            SOURCE_EC = "ECMarketplace Latam"
            BASE_EC   = "https://ecmarketplacelatam.com"
            try:
                log.info("[ECMarketplace] Cargando con Playwright...")
                page = browser.new_page()
                page.goto(
                    f"{BASE_EC}/Buscador",
                    wait_until="networkidle",
                    timeout=45000
                )
                page.wait_for_timeout(4000)

                try:
                    page.click(
                        "button:has-text('Buscar'), input[value='Buscar']",
                        timeout=3000
                    )
                    page.wait_for_timeout(3000)
                except Exception:
                    pass

                cards = page.query_selector_all(
                    ".card, .producto-card, .item-producto, "
                    "[class*='producto'], [class*='oferta'], [class*='card']"
                )
                log.info(f"[ECMarketplace] {len(cards)} tarjetas encontradas")

                seen = set()
                for card in cards[:100]:
                    try:
                        title_el = card.query_selector(
                            "h2, h3, h4, .titulo, .nombre, strong"
                        )
                        desc_el  = card.query_selector(
                            "p, .descripcion, .description"
                        )
                        link_el  = card.query_selector("a[href]")

                        title_text = title_el.inner_text().strip() if title_el else ""
                        if not title_text or title_text in seen:
                            continue
                        seen.add(title_text)

                        desc_text = (
                            desc_el.inner_text().strip() if desc_el else ""
                        )
                        href = link_el.get_attribute("href") if link_el else ""
                        if href and not href.startswith("http"):
                            href = BASE_EC + href

                        results_ecmarket.append(make_record(
                            title=title_text,
                            actor=title_text,
                            source=SOURCE_EC,
                            desc=desc_text,
                            url=href,
                        ))
                    except Exception:
                        continue

                if not results_ecmarket:
                    html = page.content()
                    soup = BeautifulSoup(html, "lxml")
                    for el in soup.select("h3, h4"):
                        text = el.get_text(strip=True)
                        if len(text) > 8:
                            results_ecmarket.append(make_record(
                                title=text, actor=text,
                                source=SOURCE_EC, desc="",
                                url=f"{BASE_EC}/Buscador",
                            ))

                page.close()
                log.info(f"[ECMarketplace] {len(results_ecmarket)} registros")

            except Exception as e:
                log.error(f"[ECMarketplace] Error: {e}")

            # Ecodirectorio SDA
            SOURCE_SDA = "Ecodirectorio SDA"
            URL_SDA = (
                "https://sites.google.com/ambientebogota.gov.co"
                "/ecodirectorio-2023/inicio"
            )
            try:
                log.info("[Ecodirectorio SDA] Cargando con Playwright...")
                page = browser.new_page()
                page.goto(URL_SDA, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(4000)

                all_links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href)"
                )

                company_urls = list({
                    link for link in all_links
                    if "ecodirectorio-2023" in link
                    and "inicio" not in link
                    and link.startswith("https://sites.google.com")
                })[:35]

                log.info(
                    f"[Ecodirectorio SDA] {len(company_urls)} perfiles encontrados"
                )

                for company_url in company_urls:
                    try:
                        page.goto(
                            company_url,
                            wait_until="domcontentloaded",
                            timeout=20000
                        )
                        page.wait_for_timeout(2000)

                        title = page.title()
                        title = re.sub(
                            r"\s*[-|]\s*(Ecodirectorio|ecodirectorio).*$",
                            "", title
                        ).strip()

                        body_text = ""
                        for selector in [
                            "[role='main']", ".tyJCtd", "article", "main"
                        ]:
                            try:
                                body_text = page.inner_text(selector)
                                if body_text.strip():
                                    break
                            except Exception:
                                continue

                        body_clean = " ".join(body_text.split())[:600]

                        if title and len(title) > 3:
                            results_ecdir.append(make_record(
                                title=title,
                                actor=title,
                                source=SOURCE_SDA,
                                desc=body_clean[:240],
                                detail=body_clean,
                                url=company_url,
                            ))
                    except Exception as e:
                        log.debug(
                            f"[Ecodirectorio SDA] Error en {company_url}: {e}"
                        )

                page.close()
                log.info(f"[Ecodirectorio SDA] {len(results_ecdir)} registros")

            except Exception as e:
                log.error(f"[Ecodirectorio SDA] Error: {e}")

            browser.close()

    except Exception as e:
        log.error(f"[Playwright] Error general: {e}")

    return results_ecmarket, results_ecdir


# POST-PROCESAMIENTO

VALID_TIPOS    = {"bien", "servicio", "tecnologia", "capacidad", "financiacion"}
VALID_SECTORES = {"agro", "construccion", "textil", "residuos", "agua"}


def validate(records):
    clean = []
    for r in records:
        if not r.get("title", "").strip() or len(r["title"]) < 3:
            continue
        if r.get("tipo")   not in VALID_TIPOS:    r["tipo"]   = "servicio"
        if r.get("sector") not in VALID_SECTORES: r["sector"] = "residuos"
        if not r.get("desc", "").strip():          r["desc"]   = r["title"]
        clean.append(r)
    return clean


def deduplicate(records):
    seen = {}
    for r in records:
        key = re.sub(r"\s+", " ", r["title"].lower().strip())[:80]
        if (key not in seen or
                len(r.get("detail", "")) > len(seen[key].get("detail", ""))):
            seen[key] = r
    result = list(seen.values())
    log.info(f"[Dedup] {len(records)} -> {len(result)} registros unicos")
    return result


# MAIN

def main():
    log.info("=" * 55)
    log.info("BRC Metabuscador v3 - Inicio de scraping")
    log.info(f"Hora UTC: {TODAY}")
    log.info("=" * 55)

    all_records = []

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        all_records += scrape_bazzarbog(client)
        all_records += scrape_negocios_verdes_api(client)

    results_ec, results_sda = scrape_con_playwright()
    all_records += results_ec
    all_records += results_sda

    all_records = validate(all_records)
    all_records = deduplicate(all_records)
    all_records.sort(key=lambda r: (r["source"], r["title"]))

    OUTPUT_FILE.write_text(
        json.dumps(all_records, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    log.info("=" * 55)
    log.info(f"data.json -> {len(all_records)} registros totales")
    sources = {r["source"] for r in all_records}
    for s in sorted(sources):
        count = sum(1 for r in all_records if r["source"] == s)
        log.info(f"  {s}: {count} registros")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
