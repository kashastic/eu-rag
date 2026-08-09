// Shared facts for /privacy and /terms, in one place so the two pages can
// never disagree with each other.
//
// CONTACT_EMAIL is published on a public page, where scrapers will read it.
//
// DECIDED 2026-08-10: a **dedicated mailbox** (e.g. eurag.privacy@…), not the
// operator's personal address and not a plus-alias — a `+tag` strips back to
// the real inbox, so it publishes the personal address either way. A dedicated
// box also survives EURAG getting a company or a custom domain.
//
// Changing this changes a published legal document — treat it like one, and
// don't point it at an inbox nobody reads.
export const CONTACT_EMAIL = "akashacharya.de@gmail.com";

// Bump whenever either page changes in substance, not for typo fixes.
export const LAST_UPDATED = "10 August 2026";
