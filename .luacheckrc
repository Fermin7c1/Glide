-- luacheck settings for the Glide addon (tests/test_lint.py runs `luacheck addon/Glide`).
-- The WoW client runs Lua 5.1; everything the game provides is a read-only global here, and the names the addon
-- itself defines for the game (SavedVariables, slash commands, key bindings) are the only globals it may set.
std = "lua51"
max_line_length = 140           -- the house style is long lines (the Python side wraps at 120, Lua a little later)
exclude_files = { "addon/_old/**" }

globals = {
    -- SavedVariables (Glide.toc)
    "GlideRecorderDB", "GlideLinkDB",
    -- slash commands and key bindings (Bindings.xml calls Glide_Mark / Glide_Trash)
    "SLASH_GLIDE1", "SLASH_GLIDECOACH1", "SLASH_GLIDECOACH2", "SLASH_GLIDELINK1", "SlashCmdList",
    "BINDING_HEADER_GLIDE", "BINDING_NAME_GLIDE_MARK", "BINDING_NAME_GLIDE_TRASH", "Glide_Mark", "Glide_Trash",
}

read_globals = {
    -- Lua helpers the client adds
    "date", "time", "strsplit", "tinsert", "hooksecurefunc",
    -- C_ namespaces and enums
    "C_AddOns", "C_Container", "C_DeathInfo", "C_EncodingUtil", "C_GossipInfo", "C_Item", "C_Map", "C_MerchantFrame",
    "C_PlayerInfo", "C_QuestLog", "C_Spell", "C_SpellBook", "C_TaxiMap", "C_Timer", "C_TooltipInfo", "C_UnitAuras",
    "Enum", "BOOKTYPE_SPELL", "SOUNDKIT",
    -- frames, mixins and UI
    "CreateFrame", "CreateVector2D", "BackdropTemplateMixin", "GameTooltip", "MerchantFrame", "Minimap", "UIParent",
    "UISpecialFrames", "WorldFrame", "PlaySound", "ReloadUI", "GetCursorPosition", "GetPhysicalScreenSize",
    "GetCoinTextureString", "InCombatLockdown",
    -- the client and the player
    "GetBuildInfo", "GetLocale", "GetRealmName", "GetServerTime", "GetTime", "GetZoneText", "GetSubZoneText",
    "GetBindLocation", "GetMoney", "GetXPExhaustion", "GetUnitSpeed", "GetPlayerFacing", "GetWeaponEnchantInfo",
    "GetInventoryItemDurability", "GetInventoryItemLink", "IsMounted", "IsResting", "IsPlayerSpell",
    "UnitAffectingCombat", "UnitCharacterPoints", "UnitClass", "UnitClassification", "UnitCreatureType", "UnitExists",
    "UnitFactionGroup", "UnitFullName", "UnitGUID", "UnitHealth", "UnitHealthMax", "UnitIsDead", "UnitIsDeadOrGhost",
    "UnitIsGhost", "UnitIsPlayer", "UnitIsQuestBoss", "UnitIsTapDenied", "UnitLevel", "UnitName", "UnitOnTaxi",
    "UnitPosition", "UnitPower", "UnitPowerMax", "UnitPowerType", "UnitRace", "UnitReaction", "UnitXP", "UnitXPMax",
    -- spells, skills, talents, trainers
    "GetSpellInfo", "GetSpellSubtext", "GetSpellBookItemName", "GetSpellBookItemInfo", "GetNumSpellTabs", "GetSpellTabInfo",
    "GetSpellDescription", "GetSpellTexture",
    "GetNumSkillLines", "GetSkillLineInfo", "GetNumTalentTabs", "GetNumTalents", "GetTalentInfo",
    "GetNumTrainerServices", "GetTrainerServiceInfo", "GetTrainerServiceCost", "GetTrainerServiceLevelReq",
    -- quests and gossip
    "GetQuestID", "GetTitleText", "GetQuestText", "GetObjectiveText", "GetProgressText", "GetRewardText",
    "GetRewardXP", "GetRewardMoney", "GetNumQuestChoices", "GetNumQuestRewards", "GetQuestItemInfo",
    "GetQuestItemLink", "GetQuestFactionGroup", "GetQuestUiMapID", "HaveQuestData", "GetNumAvailableQuests",
    "GetAvailableTitle", "GetAvailableQuestInfo",
    -- loot, bags, merchants, repairs, flight masters
    "GetNumLootItems", "GetLootSlotLink", "GetLootSourceInfo", "GetCursorInfo", "CursorHasItem", "ClearCursor",
    "DeleteCursorItem", "GetMerchantNumItems", "GetMerchantItemInfo", "GetMerchantItemLink", "CanMerchantRepair",
    "GetRepairAllCost", "RepairAllItems", "GetTaxiMapID", "TaxiNodeName", "TaxiNodeCost",
    -- other addons: RestedXP (RXP, its per-character SavedVariables RXPCData) and LibStub
    "RXP", "RXPCData", "LibStub",
}

-- generated data tables: one long line per row
files["addon/Glide/Data/"] = { max_line_length = false }
