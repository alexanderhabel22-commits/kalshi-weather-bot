"""Shared loader: maker-NO fills on settled Mentions markets."""
import re
import pandas as pd

SPORTS = re.compile(r"NBA|MLB|NFL|NCAA|WC|NHL|WNBA|FIGHT|UFC|ATHLETE|SPORT|GOLF|TENNIS|F1|SOCCER|BOX|OLYMP|SUPERBOWL|MLS|EPL|PGA")
POL = re.compile(r"TRUMP|VANCE|MAMDANI|BERNIE|HOCHUL|CARNEY|SECPRESS|BESSENT|FED|POWELL|PRES|CONGRESS|HEARING|POLITIC|GOV|SENAT|NEWSOM|BIDEN|HARRIS|MUSK|PELOSI|AOC|RUBIO|HEGSETH|LEAVITT|DEBATE|WORLDNEWS|UN|SOTU|MAYOR|NETANYAHU|PUTIN|ZELENSK|STARMER|MILEI")
MEDIA = re.compile(r"FOX|MADDOW|LASTWORD|LATENIGHT|SURVIVOR|LOVEISL|MRBEAST|MTP|SNL|PODCAST|ROGAN|TV|SHOW|OSCAR|GRAMMY|EMMY|MVE|PSAKI|TUCKER|CNN|MSNBC|TALK|VIEW|KIMMEL|FALLON|COLBERT")
EARN = re.compile(r"EARN|CALL|CEO|SPCX|TSLA|NVDA|AAPL|META|AMZN|GOOG|MSFT|COMPANY|CONF")


def stype(s: str) -> str:
    s = s.replace("MENTION", "").replace("KX", "", 1)
    for name, rx in (("sports", SPORTS), ("politics", POL), ("media", MEDIA), ("earnings", EARN)):
        if rx.search(s):
            return name
    return "other"


def band(p):
    return pd.cut(p, [0, .5, .6, .7, .75, .8, .85, .9, .95, .98, 1.0],
                  labels=["<50", "50-60", "60-70", "70-75", "75-80", "80-85", "85-90", "90-95", "95-98", "98+"],
                  right=False)
