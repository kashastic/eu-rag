// The privacy notice. Written to be *checkable*: every claim on this page
// corresponds to something in the code, and the file paths are named in the
// comments so a future session can verify a sentence rather than trust it.
//
// Deliberately NOT a cookie banner. EURAG sets no cookies at all and runs no
// analytics; the only things it stores on your device are the session tokens,
// which are exempt from the ePrivacy consent rule as strictly necessary for a
// service the user asked for. See docs/UPDATE_LOG.md — a consent banner here
// would be theatre, and a banner claiming to manage consent that isn't being
// collected would be a false statement.

import Link from "next/link";
import { CONTACT_EMAIL, LAST_UPDATED } from "@/lib/legal";

export const metadata = {
  title: "Privacy — EURAG",
  description: "What EURAG stores, why, who it is shared with, and how to erase it.",
};

export default function Privacy() {
  return (
    <div className="legal">
      <header className="legal-head">
        <Link className="brand" href="/chat">
          EURAG<span className="star">★</span>
        </Link>
        <span className="legal-date">Last updated {LAST_UPDATED}</span>
      </header>

      <h1>Privacy</h1>
      <p className="lede">
        EURAG answers EU compliance questions for small businesses. It would be a
        poor advertisement for that if it were careless with your data, so this
        page says plainly what is stored and what leaves the server.
      </p>

      <h2>No cookies, no tracking, no analytics</h2>
      <p>
        EURAG sets no cookies. There is no analytics product, no advertising
        network, and no third-party tracker on this site — which is why you are
        not being asked to accept anything. What is kept in your browser is your
        sign-in token, held in <code>localStorage</code> so that you stay signed
        in, and the business context you optionally set (country, company size,
        sector, whether you use AI). Signing out deletes the token; the context
        is cleared with your browser data, and if you have an account you can
        also clear it by setting each field back to &ldquo;No answer&rdquo;.
        Cloudflare and Google may use their own storage when their scripts run —
        see <a href="#third-parties">who else sees your data</a>.
      </p>
      <p>
        We do not guess any of that context. Nothing here infers your country
        from your IP address or from anything else — if a field is not set, it is
        simply not used.
      </p>

      <h2>What is stored on the server</h2>
      <div className="legal-scroll">
      <table className="legal-table">
        <thead>
          <tr>
            <th>Data</th>
            <th>Why</th>
            <th>Kept for</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Your IP address</td>
            <td>
              Counting free anonymous questions and rate-limiting abuse. It is
              the only way to meter someone with no account.
            </td>
            <td>About 2 days, then deleted automatically</td>
          </tr>
          <tr>
            <td>Username</td>
            <td>Your account</td>
            <td>Until you delete the account</td>
          </tr>
          <tr>
            <td>Email address and Google account id</td>
            <td>
              Only if you sign in with Google — it is how your account is
              recognised next time
            </td>
            <td>Until you delete the account</td>
          </tr>
          <tr>
            <td>Password</td>
            <td>
              Only if you register with one. Stored as a salted scrypt hash, not
              as text — nobody can read it back, including us
            </td>
            <td>Until you delete the account</td>
          </tr>
          <tr>
            <td>Your saved chats — questions and answers</td>
            <td>So you can come back to them</td>
            <td>Until you delete the chat or the account</td>
          </tr>
          <tr>
            <td>
              Your business context, if you set it — country, company size band,
              sector, and whether you use or build AI
            </td>
            <td>
              To point answers at the thresholds that apply to you. Sent with
              each question, so it reaches Anthropic like the question does.
              Only stored on the server if you have an account; otherwise it
              stays in your browser
            </td>
            <td>Until you clear it or delete the account</td>
          </tr>
          <tr>
            <td>Your Anthropic API key, if you add one</td>
            <td>
              To bill premium answers to you instead of us. Encrypted
              (AES-256-GCM) at rest
            </td>
            <td>Until you remove it or delete the account</td>
          </tr>
          <tr>
            <td>
              A security log of sign-ins, failed sign-ins and registrations
            </td>
            <td>
              Detecting attacks on accounts. Question text is never in it
            </td>
            <td>
              Kept, but your username is replaced with{" "}
              <code>deleted_account</code> when you delete the account
            </td>
          </tr>
          <tr>
            <td>Ordinary web-server logs</td>
            <td>Running and debugging the service</td>
            <td>Until the server is restarted or redeployed</td>
          </tr>
        </tbody>
      </table>
      </div>
      <p className="note">
        <strong>About that API key:</strong> encryption at rest protects it if
        the database is stolen. It does not protect it from whoever operates
        this server, because the decryption key lives on the same machine. Use a
        dedicated key with a spend limit. This is stated the same way in the app
        itself.
      </p>

      <h2 id="third-parties">Who else sees your data</h2>
      <ul>
        <li>
          <strong>Anthropic</strong> (United States) — your question, the
          relevant passages from the official documents, and the answer are
          processed by Anthropic&apos;s Claude models. This is the core of how
          EURAG works and it cannot be switched off.{" "}
          <strong>Do not put personal data or confidential business
          information in a question.</strong> Once a question is sent it is
          outside this server&apos;s control, so account deletion cannot reach
          it.
        </li>
        <li>
          <strong>Cloudflare</strong> — an invisible bot check (Turnstile) runs
          when you submit a question or create an account. Cloudflare receives
          your IP address to do it.
        </li>
        <li>
          <strong>Google</strong> — only if you press “Continue with Google”.
          Google tells us your account id and email address, nothing else, and
          we never receive your Google password.
        </li>
      </ul>
      <p>
        That is the whole list. In particular the site&apos;s typefaces are
        served from this server rather than from Google Fonts, so simply reading
        this page tells Google nothing.
      </p>
      <p>
        Nothing is sold, and nothing is shared with anyone else. There is no
        advertising on this site and no plan to add any.
      </p>

      <h2>Deleting everything</h2>
      <p>
        Sign in, open <em>Your Anthropic key</em> from the sidebar, and use{" "}
        <strong>Delete account</strong>. It removes your account, your saved
        chats, any document you uploaded, your stored API key, your business
        context, and your free-tier counter, immediately and irreversibly. The
        security log survives with
        your username stripped out, so that deleting an account cannot be used
        to erase evidence of an attack on someone else&apos;s.
      </p>
      <p>
        You can also ask for a copy of your data, or ask us to correct or erase
        it by hand, at <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
        If you think this service handles your data unlawfully you can complain
        to your national data protection authority.
      </p>

      <h2>Children</h2>
      <p>
        EURAG is a tool for businesses and is not intended for anyone under 16.
      </p>

      <h2>Changes</h2>
      <p>
        If this notice changes materially, the date at the top changes with it.
      </p>

      <footer className="legal-foot">
        <Link href="/chat">← Back to EURAG</Link>
        <Link href="/terms">Terms &amp; disclaimer</Link>
      </footer>
    </div>
  );
}
