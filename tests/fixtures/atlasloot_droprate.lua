-- A hand-made file in the shape of AtlasLootClassic_DungeonsAndRaids/droprate.lua, for tests/test_atlasloot_rates.py.
-- Three chances are the ones the plan cites (Magister's Crown 13.84, Bonecreeper Stylus 12.93, Magister's Mantle
-- 18.81); the rest are made up. Nothing here is AtlasLoot's file.
local Droprate = _G.AtlasLoot.Data.Droprate
--##START-DATA##
local DropRateData = {
	[1853] = {
		[13938] = 12.93,
		[16686] = 13.84,
		[90001] = 0, -- no chance: left out
		[90002] = 250, -- not a percent: left out
	},
	[10508] = {
		[16689] = 18.81;
		[90003] = 5
	},
	--[[ a long comment
	     with a brace } in it ]]
	["Bug Trio"] = {
		[90004] = 1.5e1,
	},
	[90100] = {
	},
}
--##END-DATA##

-- code after the data is never read, let alone run
if (AtlasLoot:GameVersion_EQ(AtlasLoot.CLASSIC_VERSION_NUM) and AtlasLoot.Data.ContentPhase:GetActivePhase() < 5) then
	DropRateData[1853][16686] = 99.99;
end
GlideTestExecuted = true
error("this file was executed")

Droprate:AddData(DropRateData)
DropRateData = nil
