# 0039. Custom folders for pricing rules

- **Status:** Accepted
- **Date:** 2026-06-20
- **Relates to:** [ADR-0007](0007-pricing-rule-taxonomy.md) (pricing rules),
  [ADR-0022](0022-no-sequential-pks-in-api.md) (a rule is addressed by its target),
  [ADR-0025](0025-uuid-primary-keys.md) (keys)

## Context

The Rules page groups rules into **auto folders** by top-level market category (the
collapsible accordion). A manager asked to also sort rules into **their own** folders
(e.g. "Moon goo", "Capital fuel") — without turning the page into a file manager.

## Decision

**A rule carries one optional free-text `folder` label; folders are emergent (they exist
only while a rule references them), and a "Group by" toggle switches the accordion between
the auto categories and the custom folders.** No separate folder-management surface.

- **Data: a nullable `folder` string column** on `pricing_rules` — not a foreign key, no
  `rule_folder` table. Set/cleared through the rule's `PUT` (full-replacement, like the hub
  override): blank/whitespace normalises to `null`, which files the rule under its market
  category instead. Capped at 64 chars.
- **Display: a `Group by: Category | My folders` toggle.** "Category" is today's behaviour
  (unchanged). "My folders" is a **split** view — only custom folders, with unfiled rules
  collected in an **"Ungrouped"** bucket (sorted last); folder names sort alphabetically.
  The accordion, table, and columns are otherwise identical, so the page isn't more crowded.
- **Assignment: a folder combobox in the rule editor** (pick an existing folder from a
  datalist, or type a new name). To re-file or otherwise change an existing rule, an **Edit**
  row action pre-loads it into that same form (the form is remounted with the rule's values,
  including the hub override, and the target is locked — changing the target would orphan the
  original rule, so that's an explicit "add a new rule" instead).
- **Manager-only.** Folders are an organisation aid on the Rules page; members never see it,
  and pricing/resolution ignore the field entirely.

## Consequences

- Zero new management UI: create a folder by typing its name; it disappears when its last
  rule leaves. The page looks unchanged in "Category" mode.
- The **Edit** action is useful beyond folders — previously a rule could only be replaced by
  re-searching its target and re-entering every field.
- **One folder per rule** for now. The string column keeps the migration path to multi-folder
  *tags* cheap and contained: introduce a `rule_folders` join table, backfill the column's
  values, drop the column — no API shape leaks the single-folder assumption beyond the one
  `folder` field. We deliberately did **not** build the join table up front (YAGNI).

## Alternatives considered / rejected

- **First-class folder entities** (rename, manual reorder, colour, empty folders): each wants
  its own UI — exactly the crowding to avoid. The emergent-string model gets the feature with
  none of it; promote later only if managers ask for reordering/colours.
- **Per-row "move to folder" menu / drag-and-drop:** more chrome (a menu per row) or a heavy,
  less-accessible interaction. The Edit form reuses an affordance worth having anyway.
- **Blending custom + auto folders in one list:** ambiguous ("is this folder mine or
  derived?"). The split view keeps the two groupings cleanly separate behind the toggle.
