// Terms and the not-legal-advice disclaimer. The disclaimer already appears
// under the composer on every answer (CLAUDE.md standing rule 2); this is the
// canonical long form of it, so the short line has somewhere to point.

import Link from "next/link";
import { CONTACT_EMAIL, LAST_UPDATED } from "@/lib/legal";

export const metadata = {
  title: "Terms — EURAG",
  description: "What EURAG is, what it is not, and the limits of relying on it.",
};

export default function Terms() {
  return (
    <div className="legal">
      <header className="legal-head">
        <Link className="brand" href="/chat">
          EURAG<span className="star">★</span>
        </Link>
        <span className="legal-date">Last updated {LAST_UPDATED}</span>
      </header>

      <h1>Terms &amp; disclaimer</h1>

      <h2 className="flag">This is information, not legal advice</h2>
      <p>
        EURAG retrieves passages from official EU and national sources and has a
        language model summarise them, with a citation on every claim. That is a
        research shortcut, not professional advice, and it does not create a
        lawyer–client relationship of any kind.
      </p>
      <p>
        <strong>Follow the citations before you act.</strong> Every answer links
        to the official text it came from — that link, not the summary, is the
        authority. Regulations are amended, national implementations differ, and
        an answer that is right in general can be wrong for your specific
        situation. For a decision with money or liability attached, consult a
        qualified professional in the relevant jurisdiction.
      </p>

      <h2>What the service does and doesn&apos;t promise</h2>
      <ul>
        <li>
          The corpus is a fixed set of official documents and is not exhaustive.
          If it cannot support an answer, EURAG is built to say so rather than
          guess — but it can still be wrong, incomplete, or out of date.
        </li>
        <li>
          Funding calls and deadlines change constantly. Treat anything about an
          open call as a pointer to the official portal, never as current fact.
        </li>
        <li>
          The service is provided as-is, with no warranty, and may be
          unavailable, rate-limited, or discontinued at any time. To the extent
          the law allows, we are not liable for any loss arising from relying on
          an answer.
        </li>
      </ul>

      <h2>Using it fairly</h2>
      <ul>
        <li>
          One account per person. Don&apos;t automate against the service,
          attempt to bypass the free-question limits, or scrape it.
        </li>
        <li>
          Don&apos;t upload anything you don&apos;t have the right to, and
          don&apos;t put personal data or confidential business information into
          a question — questions are sent to Anthropic to be answered. See{" "}
          <Link href="/privacy">Privacy</Link>.
        </li>
        <li>
          Accounts that abuse the service can be suspended without notice.
        </li>
      </ul>

      <h2>Costs</h2>
      <p>
        EURAG is free to use within the published limits. Beyond them you can
        add your own Anthropic API key, in which case answers are billed to you
        by Anthropic directly, under their terms and pricing — not by us. You
        can remove the key at any time; removing it here does not revoke it at
        Anthropic, which you must do yourself.
      </p>

      <h2>Your account</h2>
      <p>
        You can delete your account and everything in it at any time from the
        app — see <Link href="/privacy">Privacy</Link>. Note that an account
        registered with a username and password has no email attached, so{" "}
        <strong>a forgotten password cannot be recovered</strong> and the saved
        chats go with it. Signing in with Google avoids this.
      </p>

      <h2>Contact</h2>
      <p>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
      </p>

      <footer className="legal-foot">
        <Link href="/chat">← Back to EURAG</Link>
        <Link href="/privacy">Privacy</Link>
      </footer>
    </div>
  );
}
