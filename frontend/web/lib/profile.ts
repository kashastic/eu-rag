// The asker's business context: four optional, closed-vocabulary fields.
//
// Most EU obligations are threshold functions — of headcount, of member state,
// of whether you build an AI system or merely use one — so the same question
// has different correct answers for different askers.
//
// The values here MUST match `core/profile.py` exactly; the server rejects
// anything off-list with a 422 rather than letting it reach a prompt. Labels
// are display-only and live here alone.
//
// Storage: anonymous visitors keep this in localStorage and nowhere else.
// Logged-in users also get a server-side copy (GET/PUT /account/profile) so a
// second device recovers the context — the client still sends it with every
// question, so the server never reads it per query.

export type Profile = {
  country: string | null;
  size: string | null;
  sector: string | null;
  ai_role: string | null;
};

export const EMPTY_PROFILE: Profile = {
  country: null,
  size: null,
  sector: null,
  ai_role: null,
};

export const COUNTRIES: [string, string][] = [
  ["AT", "Austria"], ["BE", "Belgium"], ["BG", "Bulgaria"], ["HR", "Croatia"],
  ["CY", "Cyprus"], ["CZ", "Czechia"], ["DK", "Denmark"], ["EE", "Estonia"],
  ["FI", "Finland"], ["FR", "France"], ["DE", "Germany"], ["GR", "Greece"],
  ["HU", "Hungary"], ["IE", "Ireland"], ["IT", "Italy"], ["LV", "Latvia"],
  ["LT", "Lithuania"], ["LU", "Luxembourg"], ["MT", "Malta"],
  ["NL", "Netherlands"], ["PL", "Poland"], ["PT", "Portugal"],
  ["RO", "Romania"], ["SK", "Slovakia"], ["SI", "Slovenia"], ["ES", "Spain"],
  ["SE", "Sweden"], ["non_eu", "Outside the EU"],
];

// Headcount bands. The EU SME definition also turns on turnover and balance
// sheet, which is why the server warns the model not to settle a size category
// on headcount alone.
export const SIZES: [string, string][] = [
  ["micro", "Under 10 people"],
  ["small", "10–49 people"],
  ["medium", "50–249 people"],
  ["large", "250+ people"],
];

export const SECTORS: [string, string][] = [
  ["software", "Software & IT"],
  ["manufacturing", "Manufacturing"],
  ["retail", "Retail & e-commerce"],
  ["food", "Food & drink"],
  ["healthcare", "Healthcare & life sciences"],
  ["finance", "Financial services"],
  ["professional", "Professional services"],
  ["construction", "Construction"],
  ["transport", "Transport & logistics"],
  ["energy", "Energy & utilities"],
  ["education", "Education & training"],
  ["media", "Creative & media"],
  ["agriculture", "Agriculture"],
  ["hospitality", "Hospitality & tourism"],
  ["other", "Something else"],
];

// The AI Act distinction that actually changes an answer. A deployer's
// obligations and a provider's differ by an order of magnitude, so this asks
// for the role rather than for a yes/no.
export const AI_ROLES: [string, string][] = [
  ["none", "We don't use AI"],
  ["deployer", "We use AI tools built by others"],
  ["provider", "We build, rebrand or sell AI"],
];

export const AI_ROLE_HINT =
  "The AI Act treats these very differently: using someone else's AI tool " +
  "makes you a deployer, while building or putting your name on one makes you " +
  "a provider.";

export const FIELDS: {
  key: keyof Profile;
  label: string;
  options: [string, string][];
  hint?: string;
}[] = [
  { key: "country", label: "Country", options: COUNTRIES },
  { key: "size", label: "Company size", options: SIZES },
  { key: "sector", label: "Sector", options: SECTORS },
  { key: "ai_role", label: "AI use", options: AI_ROLES, hint: AI_ROLE_HINT },
];

const KEY = "eurag.profile.v1";
const DISMISSED = "eurag.profile.dismissed.v1";

function labelFor(options: [string, string][], value: string | null): string | null {
  if (!value) return null;
  return options.find(([v]) => v === value)?.[1] ?? null;
}

/** The collapsed one-line form, e.g. "Germany · 10–49 people · Software & IT". */
export function summarise(p: Profile): string {
  return FIELDS.map((f) => labelFor(f.options, p[f.key]))
    .filter(Boolean)
    .join(" · ");
}

export function isEmpty(p: Profile): boolean {
  return !p.country && !p.size && !p.sector && !p.ai_role;
}

/** Only send a profile when something is actually set — an all-null object in
 *  every request body is noise, and the server treats absent and empty alike. */
export function forRequest(p: Profile): Profile | undefined {
  return isEmpty(p) ? undefined : p;
}

export function load(): Profile {
  if (typeof window === "undefined") return EMPTY_PROFILE;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return EMPTY_PROFILE;
    const parsed = JSON.parse(raw) as Partial<Profile>;
    // read field by field: a stored blob from an older/newer version must not
    // introduce keys the server will reject
    return {
      country: parsed.country ?? null,
      size: parsed.size ?? null,
      sector: parsed.sector ?? null,
      ai_role: parsed.ai_role ?? null,
    };
  } catch {
    return EMPTY_PROFILE;
  }
}

export function save(p: Profile) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // private-browsing quota errors must never break asking a question
  }
}

export function isDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(DISMISSED) === "1";
  } catch {
    return false;
  }
}

export function dismiss() {
  try {
    localStorage.setItem(DISMISSED, "1");
  } catch {
    /* ignore */
  }
}
