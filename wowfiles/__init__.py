"""Readers for the game client's own files (read-only), shared by the engine, the knowledge build and the tools.

  casc         the client's CASC storage: files by FileDataID or name (+ install_of: the folder and product)
  db2          DB2 tables, with the .dbd layouts in tools/dbd; dbdefs picks (or fetches) the .dbd for a table
  db2_import   client tables exported as CSV -> the knowledge base (knowledge/sources/db2.py)
  blp          BLP textures -> RGBA, PNG
  spells       which icon each spell shows (SpellMisc)
  wdb          the client's WDB caches (quests, creatures, objects)
  adt          map tiles (WDT, ADT terrain and liquids); worldobjects: WMO and M2 collision geometry
  terrain      the 8.3 yd terrain grid (data/gamedata/terrain/<map>.npz)
  navbuild     the walking map builder; navformat: its file format and versions (no numpy)

The command lines that run them stay in tools/ (extract_maps.py, navmesh.py, build_db.py, ...). Named wowfiles, not
gamedata, because data/gamedata is the folder their output goes to.
"""
