# -*- coding: utf-8 -*-
"""
scraper.py - BRC Metabuscador v4
Fuentes:
  1. BazzarBog CCB             - HTML estatico (PrestaShop)
  2. Negocios Verdes Nacional   - API SODA datos.gov.co (filtro CAR + Bogota)
  3. Ecodirectorio SDA         - HTML estatico por categorias (135 empresas)
  4. ECMarketplace Latam        - Playwright (JS rendering)
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
REQUEST_TIMEOUT = 30
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
        "cafe", "cacao", "hortalizas", "frutas", "verduras", "panela",
        "apicultura", "miel"
    ],
    "textil": [
        "textil", "fibra", "ropa", "moda", "upcycling", "confeccion",
        "tela", "hilo", "vestimenta", "cuero", "calzado", "prendas",
        "sostenible moda", "accesorios"
    ],
    "construccion": [
        "construccion", "demolicion", "rcd", "arido", "concreto",
        "cemento", "obra", "edificio", "infraestructura", "yeso",
        "madera", "bambu", "construccion sostenible"
    ],
    "residuos": [
        "residuo", "reciclaje", "plastico", "papel", "carton",
        "electronico", "respel", "basura", "aprovechamiento",
        "punto ecologico", "reciclado", "caneca", "envase", "empaque"
    ],
}

ESTRATEGIA_KW = {
    "Diseno circular": [
        "diseno", "ecodiseno", "trazabilidad", "ciclo de vida",
        "blockchain", "sostenible", "ecologico", "verde"
    ],
    "Extension vida util": [
        "reparacion", "reutilizacion", "segunda vida", "refabricacion",
        "mantenimiento", "extension vida", "upcycling", "transformacion"
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

def scrape_negocios_verdes_api(client):
    SOURCE   = "Negocios Verdes - CAR / Bogota"
    BASE_API = "https://www.datos.gov.co/resource/v29b-znjj.json"
    results  = []

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
        log.info(f"[NegociosVerdes] {len(data)} registros recibidos")

        for item in data:
            nombre    = (item.get("raz_n_social_del_negocio") or "").strip()
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

            if not nombre:
                continue

            loc  = f"{municipio}, {dpto}".strip(", ") or "Bogota Region"
            desc = desc_raw[:240] if desc_raw else (
                f"Negocio verde verificado por {autoridad}. "
                f"Categoria: {categoria}. Producto: {producto}."
            )
            detail = (
                f"{desc_raw} Sector: {sector}. Subsector: {subsector}. "
                f"Producto: {producto}. Autoridad: {autoridad}. "
                f"Representante: {rep}."
            ).strip()

            results.append(make_record(
                title=nombre, actor=nombre, source=SOURCE,
                desc=desc, detail=detail, loc=loc,
                anno=anno or None,
                url="https://www.negociosverdes.gov.co",
            ))

    except Exception as e:
        log.error(f"[NegociosVerdes] Error: {e}")

    log.info(f"[NegociosVerdes] {len(results)} registros procesados")
    return results


# FUENTE 3 - ECODIRECTORIO SDA (HTML estatico por categorias)
# 135 negocios verdes verificados por la SDA en 11 categorias
# Estrategia: Playwright descubre las URLs de categorias,
# luego httpx + BeautifulSoup raspa cada una.

# Categorias conocidas (hardcoded como fallback)
ECODIR_CATEGORIAS_CONOCIDAS = [
    "alimentos",
    "moda-sostenible",
    "aprovechamiento-de-residuos",
    "agricultura-sostenible",
    "tecnologias-limpias",
    "construccion-sostenible",
    "ecoturismo",
    "servicios-ambientales",
    "energia-renovable",
    "biocomercio",
    "envases-y-empaques",
]

ECODIR_BASE = (
    "https://sites.google.com/ambientebogota.gov.co"
    "/ecodirectorioempresarial"
)


def _ecodir_scrape_categoria(client, cat_url, source):
    """Raspa una pagina de categoria del Ecodirectorio con BeautifulSoup."""
    results = []
    try:
        res = client.get(cat_url, timeout=REQUEST_TIMEOUT)
        if res.status_code != 200:
            return results
        soup = BeautifulSoup(res.text, "lxml")

        # Google Sites renderiza el contenido en divs con role=main
        # o en la clase .tyJCtd. El texto de cada empresa viene en parrafos.
        main = (
            soup.select_one("[role='main']")
            or soup.select_one(".tyJCtd")
            or soup.select_one("main")
            or soup.body
        )
        if not main:
            return results

        # Cada empresa es un bloque de texto separado por <p> o <div>
        # El patron es: NOMBRE EMPRESA - descripcion. Numero de contacto: X
        raw_text = main.get_text(separator="\n", strip=True)
        blocks   = re.split(r"\n{2,}", raw_text)

        for block in blocks:
            block = block.strip()
            if len(block) < 20:
                continue
            # Ignorar navegacion y pie de pagina
            if any(skip in block.lower() for skip in [
                "search this site", "skip to", "report abuse",
                "page details", "www.ambientebogota"
            ]):
                continue

            # Primera linea suele ser el nombre de la empresa
            lines      = [l.strip() for l in block.split("\n") if l.strip()]
            title_line = lines[0] if lines else block[:80]
            desc_text  = " ".join(lines[1:])[:240] if len(lines) > 1 else block[:240]

            # Limpiar el titulo: quitar "NOMBRE - Producto" si viene junto
            title_parts = re.split(r"\s*[-–]\s*", title_line, maxsplit=1)
            title       = title_parts[0].strip()[:120]
            if len(title_parts) > 1 and not desc_text:
                desc_text = title_parts[1].strip()[:240]

            if len(title) < 4:
                continue

            results.append(make_record(
                title=title,
                actor=title,
                source=source,
                desc=desc_text or title,
                detail=block[:600],
                url=cat_url,
            ))

    except Exception as e:
        log.debug(f"[Ecodirectorio] Error en {cat_url}: {e}")

    return results


def scrape_ecodirectorio_sda(client):
    """
    Raspa el Ecodirectorio SDA - Negocios Verdes.
    Paso 1: intentar descubrir categorias via Playwright.
    Paso 2: si Playwright no disponible, usar lista hardcoded.
    Paso 3: raspar cada categoria con httpx + BeautifulSoup.
    """
    SOURCE   = "Ecodirectorio SDA"
    BASE_NV  = f"{ECODIR_BASE}/negocios-verdes"
    results  = []

    # Intentar descubrir categorias con Playwright
    cat_urls = _ecodir_discover_categories(BASE_NV)

    # Fallback: usar categorias conocidas
    if not cat_urls:
        log.info("[Ecodirectorio SDA] Usando categorias hardcoded")
        cat_urls = [
            f"{BASE_NV}/{cat}"
            for cat in ECODIR_CATEGORIAS_CONOCIDAS
        ]

    log.info(f"[Ecodirectorio SDA] {len(cat_urls)} categorias a raspar")

    for cat_url in cat_urls:
        log.info(f"[Ecodirectorio SDA] Raspando: {cat_url}")
        cat_results = _ecodir_scrape_categoria(client, cat_url, SOURCE)
        log.info(f"[Ecodirectorio SDA] {len(cat_results)} empresas en {cat_url.split('/')[-1]}")
        results.extend(cat_results)

    log.info(f"[Ecodirectorio SDA] {len(results)} registros totales")
    return results


def _ecodir_discover_categories(index_url):
    """
    Usa Playwright para cargar la pagina indice y extraer
    las URLs de las categorias del menu de navegacion.
    """
    cat_urls = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            page.goto(index_url, wait_until="networkidle", timeout=40000)
            page.wait_for_timeout(3000)

            # Extraer todos los links del mismo sitio
            all_links = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
            cat_urls = list({
                link for link in all_links
                if "ecodirectorioempresarial/negocios-verdes/" in link
                and link.rstrip("/") != index_url.rstrip("/")
                and "?" not in link
            })
            browser.close()
            log.info(
                f"[Ecodirectorio SDA] Playwright encontro "
                f"{len(cat_urls)} categorias"
            )
    except Exception as e:
        log.warning(f"[Ecodirectorio SDA] Playwright no disponible: {e}")

    return cat_urls


# FUENTE 4 - ECMARKETPLACE (Playwright)

def scrape_ecmarketplace():
    SOURCE  = "ECMarketplace Latam"
    BASE_EC = "https://ecmarketplacelatam.com"
    results = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("[ECMarketplace] Playwright no instalado. Skipping.")
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            log.info("[ECMarketplace] Cargando con Playwright...")
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
                    title_text = (
                        title_el.inner_text().strip() if title_el else ""
                    )
                    if not title_text or title_text in seen:
                        continue
                    seen.add(title_text)
                    desc_text = (
                        desc_el.inner_text().strip() if desc_el else ""
                    )
                    href = link_el.get_attribute("href") if link_el else ""
                    if href and not href.startswith("http"):
                        href = BASE_EC + href
                    results.append(make_record(
                        title=title_text, actor=title_text,
                        source=SOURCE, desc=desc_text, url=href,
                    ))
                except Exception:
                    continue

            browser.close()
            log.info(f"[ECMarketplace] {len(results)} registros")

    except Exception as e:
        log.error(f"[ECMarketplace] Error: {e}")

    return results


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
    log.info("BRC Metabuscador v4 - Inicio de scraping")
    log.info(f"Hora UTC: {TODAY}")
    log.info("=" * 55)

    all_records = []

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        all_records += scrape_bazzarbog(client)
        all_records += scrape_negocios_verdes_api(client)
        all_records += scrape_ecodirectorio_sda(client)

    all_records += scrape_ecmarketplace()

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
