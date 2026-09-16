# Bundled data

`wordlist.txt.gz` is the dictionary TY-009 checks against. It is the
intersection of a frequency-ranked English word list with a full English
dictionary, which removes both obscure entries and corpus junk, plus a
supplement of investment banking and corporate finance vocabulary that a
general dictionary rejects.

Sources: [dwyl/english-words](https://github.com/dwyl/english-words) (public
domain), [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords)
(CC-BY-SA-4.0) and
[first20hours/google-10000-english](https://github.com/first20hours/google-10000-english)
(public domain).

It is deliberately compact rather than exhaustive. A larger dictionary produces
fewer false positives but catches fewer real typos; this one is sized so that
TY-009 is useful on banking prose, and the rule is disabled by default in any
case. Clients add their own vocabulary through `hygiene.dictionary` in the
profile rather than by editing this file.
