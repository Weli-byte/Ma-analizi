# Transfermarkt: decision (2026-10-02) -- NOT USED

Question: use Transfermarkt (squads, injuries, lineups for all leagues) as a free data source?
Answer: **no.**

Evidence (read from the live Transfermarkt terms page, https://www.transfermarkt.com/intern/anb,
section 11.1 "Copyright / Rights of use"): "The User is not permitted to access or copy the Digital
Content using bots, spiders, screen scraping or other automated processes. The user is also
prohibited from using the digital content for the training or development of artificial
intelligence (AI), including language models, machine learning, neural networks or other AI
systems. Uses for text and data mining (Section 44b UrhG) are expressly reserved."

- There is no official API. Any access to its pages is automated access, which the terms forbid.
- This project trains and evaluates machine-learning and LLM forecasting models, which the terms
  forbid explicitly. A technically working scraper would not change that.
- Third-party mirrors (e.g. the CC0-labelled `dcaribou/transfermarkt-datasets`, updates paused
  since 2026-07-06, no injuries table) republish the same scraped content; the CC0 label of a
  mirror does not grant rights Transfermarkt's terms withhold. Not used either.
- `robots.txt` allows generic crawlers, but robots.txt is not a licence; the terms above govern.

Sources that are used instead are listed per capability in `src/ingestion/capabilities.py`.
For lineups/injuries beyond the EPL the realistic free option is API-Football's free plan (owner
creates the account and key); a licensed commercial feed is the production answer.
