# -*- coding: utf-8 -*-
"""HOTFIX: scrub the orphaned 'Pull Invoiceable Service Lines' button from any
STORED ir_ui_view arch before the view files reload.

Problem
-------
Round-3 (v19.0.1.70.0) removed the round-2 header button

    <button name="action_pull_invoiceable_service_lines" .../>

and deleted the sale.order method it called. The view FILE is clean, but the
button is still present in the DB-stored arch (ir_ui_view.arch_db). On
``-u hksf_rental`` Odoo loads + validates the stored/combined arch of the
inherited view BEFORE post-migration can run, and validation explodes with:

    action_pull_invoiceable_service_lines is not a valid action on sale.order

Why a PRE-migration (and raw SQL)
---------------------------------
Pre-migration of the version being upgraded TO runs *before* this module's
data/view files are loaded -- the only window in which we can fix the stored
arch before validation sees it. The ORM/registry is in a fragile half-loaded
state here and the model method is already gone, so we operate on the raw
``cr`` and treat arch_db as text/jsonb.

What it does
------------
1. Find every ir_ui_view whose arch_db (jsonb in Odoo 19, possibly multi-lang
   like {"en_US": "<xml/>"}) contains the dead action name. The scan is over
   ALL views -- not just this module's xmlids -- so duplicate / Studio /
   manually-customized copies of the form are caught too.
2. For each matching arch value, drop the whole
   <button name="action_pull_invoiceable_service_lines">...</button> element
   (with any nested children) via lxml parse + remove + reserialize. If a value
   is a fragment lxml can't parse standalone, fall back to a DOTALL regex strip.
3. Write the cleaned value back, preserving the jsonb per-lang structure.
4. Idempotent: re-running after the button is gone matches nothing and is a
   no-op. Logs matched row ids/names and per-row / total change counts.
"""
import json
import logging
import re

_logger = logging.getLogger(__name__)

ACTION_NAME = 'action_pull_invoiceable_service_lines'

# DOTALL fallback: strip a whole <button ...action_pull_invoiceable_service_lines...>...</button>
# (non-greedy so it stops at the first matching close tag).
_BUTTON_RE = re.compile(
    r'<button\b[^>]*\b' + re.escape(ACTION_NAME) + r'\b.*?</button>',
    re.DOTALL,
)


def _strip_button_lxml(xml_text):
    """Remove the orphaned button via lxml. Returns the cleaned string, or
    None if parsing failed (caller should use the regex fallback)."""
    try:
        from lxml import etree
    except ImportError:
        return None
    try:
        # recover=True tolerates the slightly-loose arch Odoo stores.
        parser = etree.XMLParser(recover=True, resolve_entities=False)
        root = etree.fromstring(xml_text.encode('utf-8'), parser=parser)
    except Exception:
        return None
    if root is None:
        return None
    removed = 0
    for btn in root.xpath('//button[@name=$n]', n=ACTION_NAME):
        parent = btn.getparent()
        if parent is not None:
            parent.remove(btn)
            removed += 1
    if not removed:
        # lxml parsed but found nothing to drop (e.g. name in an attribute we
        # don't target) -- signal "no lxml change" so the regex fallback runs.
        return None
    return etree.tostring(root, encoding='unicode')


def _clean_value(xml_text):
    """Clean one arch string. Prefer lxml; fall back to regex. Returns the
    cleaned text (unchanged if nothing matched)."""
    if not xml_text or ACTION_NAME not in xml_text:
        return xml_text
    cleaned = _strip_button_lxml(xml_text)
    if cleaned is not None and ACTION_NAME not in cleaned:
        return cleaned
    # lxml unavailable, failed to parse, or didn't fully remove it -> regex.
    return _BUTTON_RE.sub('', xml_text)


def migrate(cr, version):
    cr.execute(
        "SELECT id, name, arch_db "
        "FROM ir_ui_view "
        "WHERE arch_db::text LIKE %s",
        ('%' + ACTION_NAME + '%',),
    )
    rows = cr.fetchall()
    if not rows:
        _logger.info(
            "hksf_rental 1.71.0: no stored view arch references %s -- nothing "
            "to scrub.", ACTION_NAME,
        )
        return

    _logger.info(
        "hksf_rental 1.71.0: %s stored view(s) still reference %s: %s",
        len(rows), ACTION_NAME,
        ', '.join('id=%s(%s)' % (r[0], r[1]) for r in rows),
    )

    fixed = 0
    for view_id, name, arch_db in rows:
        # arch_db comes back as the jsonb value. psycopg may hand us a dict
        # (jsonb decoded) or a JSON string -- normalise to a {lang: xml} dict.
        value = arch_db
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                # Not JSON (very old text column) -- treat as a single XML blob.
                value = {'__raw__': value}
        if not isinstance(value, dict):
            value = {'__raw__': value}

        new_value = {}
        changed = False
        for lang, xml_text in value.items():
            if not isinstance(xml_text, str):
                new_value[lang] = xml_text
                continue
            cleaned = _clean_value(xml_text)
            if cleaned != xml_text:
                changed = True
            new_value[lang] = cleaned

        if not changed:
            continue

        if '__raw__' in new_value:
            # Original was a bare text blob; write it back as plain text via
            # ::jsonb of a JSON string is wrong here, so cast the raw XML.
            cr.execute(
                "UPDATE ir_ui_view SET arch_db = to_jsonb(%s::text) WHERE id = %s",
                (new_value['__raw__'], view_id),
            )
        else:
            cr.execute(
                "UPDATE ir_ui_view SET arch_db = %s::jsonb WHERE id = %s",
                (json.dumps(new_value), view_id),
            )
        fixed += 1
        _logger.info(
            "hksf_rental 1.71.0: scrubbed orphaned %s button from view "
            "id=%s (%s).", ACTION_NAME, view_id, name,
        )

    _logger.info(
        "hksf_rental 1.71.0: stale-arch hotfix complete -- cleaned %s of %s "
        "matching view(s).", fixed, len(rows),
    )
