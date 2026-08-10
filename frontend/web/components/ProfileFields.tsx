"use client";

// The business-context controls — the schedule of facts attached to the
// question. Three presentations:
//
//   <ProfileFields>  — the fields themselves, used by the intro block and by
//                      the editor dialog.
//   <ProfileIntro>   — the intro-screen block. It sits above the starters and
//                      never blocks anything: the composer stays live, every
//                      field is optional, and Skip puts it away for good.
//                      EURAG is anonymous-first on purpose, and a form in front
//                      of the first question would be the same mistake the
//                      always-on Turnstile checkbox was.
//   <ProfileSummary> — the collapsed one-liner above the composer once set.

import { FIELDS, SIZES, type Profile, summarise } from "@/lib/profile";

// Where EU law keeps redrawing the line, in people. These sit on the
// boundaries between the four bands, so the control shows the thresholds it is
// actually asking about rather than four opaque buckets.
const STATUTORY_LINES = ["10", "50", "250"];

// The bar shows the ranges alone — its legend already says "people", and four
// nowrap "50–249 people" labels cannot fit a phone screen. Derived from SIZES
// rather than written out again, so there is no fourth copy of the vocabulary
// to drift.
const shortLabel = (label: string) => label.replace(/\s*people$/, "").replace(/^Under\s/, "<");

/** Company size, as a bar with the statutory lines marked on it.
 *  Radios rather than a listbox: four ordered bands are a segmented choice, and
 *  native radios bring arrow-key navigation and correct announcement for free. */
function ThresholdField({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (next: string | null) => void;
}) {
  return (
    <fieldset className="threshold">
      <legend>How many people</legend>
      <div className="threshold-bar">
        {SIZES.map(([v, label]) => (
          <div key={v}>
            <input
              type="radio"
              id={`size-${v}`}
              name="profile-size"
              value={v}
              checked={value === v}
              // clicking the selected band clears it — every field is optional,
              // so there has to be a way back to "no answer"
              onClick={() => value === v && onChange(null)}
              onChange={() => onChange(v)}
            />
            <label htmlFor={`size-${v}`}>{shortLabel(label)}</label>
          </div>
        ))}
      </div>
      <div className="threshold-marks" aria-hidden="true">
        {STATUTORY_LINES.map((n, i) => (
          <span key={n} style={{ left: `${((i + 1) / SIZES.length) * 100}%` }}>
            {n}
          </span>
        ))}
      </div>
      <p className="threshold-note">
        Headcount alone doesn&apos;t settle it — turnover counts too.
      </p>
    </fieldset>
  );
}

export function ProfileFields({
  profile,
  onChange,
  showHints = true,
}: {
  profile: Profile;
  onChange: (next: Profile) => void;
  showHints?: boolean;
}) {
  return (
    <div className="profile-fields">
      <ThresholdField
        value={profile.size}
        onChange={(size) => onChange({ ...profile, size })}
      />
      {FIELDS.filter((f) => f.key !== "size").map((f) => (
        <label key={f.key} className="profile-field">
          <span className="profile-label">{f.label}</span>
          <select
            value={profile[f.key] ?? ""}
            onChange={(e) => onChange({ ...profile, [f.key]: e.target.value || null })}
          >
            <option value="">No answer</option>
            {f.options.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          {showHints && f.hint && <span className="profile-hint">{f.hint}</span>}
        </label>
      ))}
    </div>
  );
}

export function ProfileIntro({
  profile,
  onChange,
  onSkip,
}: {
  profile: Profile;
  onChange: (next: Profile) => void;
  onSkip: () => void;
}) {
  return (
    <section className="profile-intro">
      <div className="profile-intro-head">
        <div>
          <h2>Who is asking</h2>
          <p>
            Optional. Most EU duties switch on at a headcount, a border or a
            role — answer these and the reply points at the lines that apply to
            you.
          </p>
        </div>
        <button className="profile-skip" onClick={onSkip}>
          Skip
        </button>
      </div>
      <ProfileFields profile={profile} onChange={onChange} />
    </section>
  );
}

export function ProfileSummary({ profile, onEdit }: { profile: Profile; onEdit: () => void }) {
  return (
    <div className="profile-summary">
      <span className="profile-summary-text">{summarise(profile)}</span>
      <button onClick={onEdit}>Edit</button>
    </div>
  );
}
