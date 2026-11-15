import sys; sys.path.insert(0,sys.argv[2]); from pol import *
for met in ("low","high"):
  for pos in ("need","holds"):
    G=[r for r in R if r["metric"]==met and r["pos"]==pos]
    B=policy(G)
    print(f"\n#### {met} {pos}   (Oct25-Aug26 | Sep-Nov26)")
    print(row("baseline (live rule)",B))
    for lab,f in [("local hr <13",lambda r:r["lhr"]<13),("local hr 13-15",lambda r:13<=r["lhr"]<16),("local hr 16-18",lambda r:16<=r["lhr"]<19),("local hr 19+",lambda r:r["lhr"]>=19),
      ("at extreme now (togo>-1)",lambda r:r["togo"]>-1),("1-3F off",lambda r:-3<r["togo"]<=-1),("3-6F off",lambda r:-6<r["togo"]<=-3),("6F+ off",lambda r:r["togo"]<=-6),
      ("tr1 toward extreme >=1F",lambda r:r["tr1"]>=1),("tr1 flat",lambda r:-1<r["tr1"]<1),("tr1 away >=1F",lambda r:r["tr1"]<=-1),
      ("tr3 toward >=2",lambda r:r["tr3"]>=2),("tr3 away >=2",lambda r:r["tr3"]<=-2),
      ("gap 1",lambda r:r["gap"]==1),("gap 2",lambda r:r["gap"]==2),("gap 3-4",lambda r:3<=r["gap"]<=4),("gap 5+",lambda r:r["gap"]>=5),
      ("room 0",lambda r:r["room"]==0),("room 1",lambda r:r["room"]==1),("room 2+",lambda r:r["room"]>=2 ),
      ("px<50",lambda r:r["px"]<.5),("px 50-80",lambda r:.5<=r["px"]<.8),("px 80-90",lambda r:.8<=r["px"]<.9),("px 90+",lambda r:r["px"]>=.9),
      ("sp 4-7",lambda r:r["sp"]<.075),("sp 8+",lambda r:r["sp"]>=.075)]:
        print(row("  "+lab,[r for r in B if f(r)]))
