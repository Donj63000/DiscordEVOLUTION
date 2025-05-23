package org.example;

import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.block.Chest;
import org.bukkit.block.CreatureSpawner;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.ConfigurationSection;
import org.bukkit.configuration.file.YamlConfiguration;
import org.bukkit.entity.*;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerMoveEvent;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.MerchantRecipe;
import org.bukkit.inventory.meta.ItemMeta;

import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;
import org.bukkit.scoreboard.*;

import java.io.File;
import java.io.IOException;
import java.util.*;

/**
 * Commande /eleveur :
 *   1) Donne un bâton « Sélecteur d’élevage ».
 *   2) On clique 2 blocs (même hauteur) pour définir la zone.
 *   3) Le plugin génère :
 *       - Cadre (clôture) + coffres + spawners d’animaux + PNJ « Éleveur » + 2 golems.
 *   4) Tant qu’il y a <= 5 animaux d’une espèce, on les laisse en paix.
 *      Dès qu’une espèce dépasse 5 individus, l’éleveur « tue » l’excès (5% chance viande cuite),
 *      loots stockés dans les coffres.
 *   5) Spawners indestructibles en mode Survie (on empêche le cassage).
 *   6) Si tous les coffres sont cassés => session arrêtée (PNJ et golems disparus).
 *   7) Persistance dans ranches.yml.
 *   8) Scoreboard qui s’affiche lorsque le joueur est DANS l’enclos, et disparaît en dehors.
 *   9) PNJ Éleveur propose un inventaire de trade spécial (64 cru => 1 diamant, 64 cuit => 2 diamants).
 */
public final class Eleveur implements CommandExecutor, Listener {

    // Nom du bâton de sélection
    private static final String RANCH_SELECTOR_NAME = ChatColor.GOLD + "Sélecteur d'élevage";

    // Liste d’espèces majeures
    private static final List<EntityType> MAIN_SPECIES = Arrays.asList(
            EntityType.CHICKEN,
            EntityType.COW,
            EntityType.PIG,
            EntityType.SHEEP
    );

    private final JavaPlugin plugin;

    // Liste des sessions actives
    private final List<RanchSession> sessions = new ArrayList<>();

    // Fichier ranches.yml
    private final File ranchFile;
    private final YamlConfiguration ranchYaml;

    // Sélections en cours : joueur -> (corner1, corner2)
    private final Map<UUID, Selection> selections = new HashMap<>();

    // Pour gérer le scoreboard (un par joueur)
    // S’il n’est pas présent dans un enclos, on lui retire le scoreboard
    private final Map<UUID, Scoreboard> playerScoreboards = new HashMap<>();

    // Task pour gérer l’affichage scoreboard
    private BukkitRunnable scoreboardTask;

    public Eleveur(JavaPlugin plugin) {
        this.plugin = plugin;

        // Lie la commande /eleveur
        if (plugin.getCommand("eleveur") != null) {
            plugin.getCommand("eleveur").setExecutor(this);
        }

        // Écoute des events
        Bukkit.getPluginManager().registerEvents(this, plugin);

        // Prépare ranches.yml
        this.ranchFile = new File(plugin.getDataFolder(), "ranches.yml");
        this.ranchYaml = YamlConfiguration.loadConfiguration(ranchFile);

        // Lance un task qui met à jour le scoreboard pour tous les joueurs
        startScoreboardLoop();
    }

    /* ============================================
     *        Gestion de la commande /eleveur
     * ============================================ */
    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (!(sender instanceof Player player)) {
            sender.sendMessage(ChatColor.RED + "Commande réservée aux joueurs.");
            return true;
        }

        if (!cmd.getName().equalsIgnoreCase("eleveur")) {
            return false;
        }

        // Donne le bâton spécial
        giveRanchSelector(player);

        // Initialise la sélection du joueur
        selections.put(player.getUniqueId(), new Selection());

        player.sendMessage(ChatColor.GREEN + "Tu as reçu le bâton de sélection d'élevage !");
        player.sendMessage(ChatColor.YELLOW + "Clique 2 blocs (même hauteur) pour définir l'enclos.");
        return true;
    }

    private void giveRanchSelector(Player player) {
        ItemStack stick = new ItemStack(Material.STICK, 1);
        ItemMeta meta = stick.getItemMeta();
        if (meta != null) {
            meta.setDisplayName(RANCH_SELECTOR_NAME);
            stick.setItemMeta(meta);
        }
        player.getInventory().addItem(stick);
    }

    /* ============================================
     *    Écoute des clics (PlayerInteractEvent)
     * ============================================ */
    @EventHandler
    public void onPlayerInteract(PlayerInteractEvent event) {
        if (event.getAction() != org.bukkit.event.block.Action.LEFT_CLICK_BLOCK
                && event.getAction() != org.bukkit.event.block.Action.RIGHT_CLICK_BLOCK) {
            return;
        }

        Player player = event.getPlayer();
        ItemStack inHand = event.getItem();
        Block clicked = event.getClickedBlock();
        if (clicked == null) return;

        // Vérifie bâton "Sélecteur d'élevage"
        if (inHand == null || inHand.getType() != Material.STICK) {
            return;
        }
        if (!inHand.hasItemMeta()) {
            return;
        }
        if (!RANCH_SELECTOR_NAME.equals(inHand.getItemMeta().getDisplayName())) {
            return;
        }

        // Empêcher l'action par défaut
        event.setCancelled(true);

        // Récupère la sélection pour ce joueur
        Selection sel = selections.get(player.getUniqueId());
        if (sel == null) {
            player.sendMessage(ChatColor.RED + "Refais /eleveur pour obtenir le bâton de sélection !");
            return;
        }

        if (sel.corner1 == null) {
            sel.corner1 = clicked;
            player.sendMessage(ChatColor.AQUA + "Coin 1 sélectionné : " + coords(clicked));
        } else if (sel.corner2 == null) {
            sel.corner2 = clicked;
            player.sendMessage(ChatColor.AQUA + "Coin 2 sélectionné : " + coords(clicked));
            // On valide
            validateSelection(player, sel);
        } else {
            // Redéfinir corner1
            sel.corner1 = clicked;
            sel.corner2 = null;
            player.sendMessage(ChatColor.AQUA + "Coin 1 redéfini : " + coords(clicked));
        }
    }

    private String coords(Block b) {
        return "(" + b.getX() + ", " + b.getY() + ", " + b.getZ() + ")";
    }

    private void validateSelection(Player player, Selection sel) {
        Block c1 = sel.corner1;
        Block c2 = sel.corner2;
        if (c1 == null || c2 == null) return;

        if (c1.getY() != c2.getY()) {
            player.sendMessage(ChatColor.RED + "Les 2 blocs doivent être à la même hauteur !");
            sel.corner2 = null;
            return;
        }

        World w = c1.getWorld();
        int y = c1.getY();
        int x1 = c1.getX(), x2 = c2.getX();
        int z1 = c1.getZ(), z2 = c2.getZ();

        int minX = Math.min(x1, x2);
        int maxX = Math.max(x1, x2);
        int minZ = Math.min(z1, z2);
        int maxZ = Math.max(z1, z2);

        int width  = (maxX - minX) + 1;
        int length = (maxZ - minZ) + 1;

        Location origin = new Location(w, minX, y, minZ);
        RanchSession rs = new RanchSession(plugin, origin, width, length);
        rs.start();
        sessions.add(rs);

        player.sendMessage(ChatColor.GREEN + "Enclos créé (" + width + "×" + length + ") !");
        saveAllSessions();

        // On nettoie la sélection
        selections.remove(player.getUniqueId());
    }

    /* ============================================
     *   Événements BlockBreakEvent
     * ============================================ */
    @EventHandler
    public void onBlockBreak(BlockBreakEvent event) {
        Block block = event.getBlock();
        Player p = event.getPlayer();

        // 1) Interdire le cassage d’un spawner de l’éleveur
        if (block.getType() == Material.SPAWNER && !p.isOp()) {
            // On vérifie si c’est dans un ranch => on empêche
            for (RanchSession rs : sessions) {
                if (rs.isInside(block.getLocation())) {
                    // Annulation
                    event.setCancelled(true);
                    p.sendMessage(ChatColor.RED + "Ce spawner est protégé, vous ne pouvez pas le casser !");
                    return;
                }
            }
        }

        // 2) S’il s’agit d’un coffre d’une session
        for (RanchSession rs : new ArrayList<>(sessions)) {
            if (rs.isChestBlock(block)) {
                rs.removeChest(block);
                if (!rs.hasChests()) {
                    // Plus de coffres => on arrête
                    rs.stop();
                    sessions.remove(rs);
                    saveAllSessions();
                }
                break;
            }
        }
    }

    /* ============================================
     *             Persistance
     * ============================================ */
    public void saveAllSessions() {
        ranchYaml.set("ranches", null);
        int i = 0;
        for (RanchSession rs : sessions) {
            ranchYaml.createSection("ranches." + i, rs.toMap());
            i++;
        }
        try {
            ranchYaml.save(ranchFile);
        } catch (IOException e) {
            e.printStackTrace();
            plugin.getLogger().severe("[Eleveur] Impossible de sauvegarder ranches.yml !");
        }
    }

    public void loadSavedSessions() {
        ConfigurationSection root = ranchYaml.getConfigurationSection("ranches");
        if (root == null) return;

        int loaded = 0;
        for (String key : root.getKeys(false)) {
            ConfigurationSection sec = root.getConfigurationSection(key);
            if (sec == null) continue;

            String worldUID = sec.getString("world", "");
            World w = Bukkit.getWorld(UUID.fromString(worldUID));
            if (w == null) {
                plugin.getLogger().warning("[Eleveur] Monde introuvable : " + worldUID);
                continue;
            }
            int bx = sec.getInt("x");
            int by = sec.getInt("y");
            int bz = sec.getInt("z");
            int width  = sec.getInt("width");
            int length = sec.getInt("length");

            // On va nettoyer l’ancienne zone (PNJ, golems)
            Location origin = new Location(w, bx, by, bz);
            clearZone(origin, width, length,
                    List.of("Éleveur", "Golem Éleveur")); // noms qu’on va donner

            RanchSession rs = new RanchSession(plugin, origin, width, length);

            // On restaure après un court délai
            Bukkit.getScheduler().runTaskLater(plugin, rs::start, 20L);
            sessions.add(rs);
            loaded++;
        }
        plugin.getLogger().info("[Eleveur] " + loaded + " enclos rechargé(s).");
    }

    public void stopAllRanches() {
        for (RanchSession rs : sessions) {
            rs.stop();
        }
        sessions.clear();
    }

    private void clearZone(Location origin, int width, int length, List<String> relevantNames) {
        World w = origin.getWorld();
        int minX = origin.getBlockX() - 2;
        int maxX = origin.getBlockX() + width + 2;
        int minZ = origin.getBlockZ() - 2;
        int maxZ = origin.getBlockZ() + length + 2;

        // Charge les chunks
        int minChunkX = minX >> 4;
        int maxChunkX = maxX >> 4;
        int minChunkZ = minZ >> 4;
        int maxChunkZ = maxZ >> 4;
        for (int cx = minChunkX; cx <= maxChunkX; cx++) {
            for (int cz = minChunkZ; cz <= maxChunkZ; cz++) {
                w.getChunkAt(cx, cz).load();
            }
        }

        w.getEntities().forEach(e -> {
            String name = ChatColor.stripColor(e.getCustomName());
            if (name == null) return;
            if (!relevantNames.contains(name)) return;

            Location l = e.getLocation();
            int x = l.getBlockX();
            int z = l.getBlockZ();
            if (x >= minX && x <= maxX && z >= minZ && z <= maxZ) {
                e.remove();
            }
        });
    }

    /* ============================================
     *   Boucle scoreboard
     * ============================================ */
    private void startScoreboardLoop() {
        scoreboardTask = new BukkitRunnable() {
            @Override
            public void run() {
                // Pour chaque joueur connecté, on regarde s’il est dans un enclos
                for (Player p : Bukkit.getOnlinePlayers()) {
                    RanchSession inside = findSessionForPlayer(p);
                    if (inside == null) {
                        // Pas dans un enclos => retire scoreboard
                        removeScoreboard(p);
                    } else {
                        // Affiche / met à jour scoreboard
                        updateScoreboard(p, inside);
                    }
                }
            }
        };
        scoreboardTask.runTaskTimer(plugin, 20L, 20L); // chaque seconde
    }

    private RanchSession findSessionForPlayer(Player p) {
        // On peut être dans 0 ou 1 enclos => on renvoie le premier qui match
        Location loc = p.getLocation();
        for (RanchSession rs : sessions) {
            if (rs.isInside(loc)) {
                return rs;
            }
        }
        return null;
    }

    private void removeScoreboard(Player p) {
        if (playerScoreboards.containsKey(p.getUniqueId())) {
            p.setScoreboard(Bukkit.getScoreboardManager().getNewScoreboard());
            playerScoreboards.remove(p.getUniqueId());
        }
    }

    private void updateScoreboard(Player p, RanchSession session) {
        // Si scoreboard déjà existant pour ce joueur, on le réutilise
        Scoreboard sb = playerScoreboards.get(p.getUniqueId());
        if (sb == null) {
            sb = Bukkit.getScoreboardManager().getNewScoreboard();
            playerScoreboards.put(p.getUniqueId(), sb);
        }

        // On crée un objectif
        Objective obj = sb.getObjective("ranchInfo");
        if (obj == null) {
            obj = sb.registerNewObjective("ranchInfo", Criteria.DUMMY, ChatColor.GOLD + "Enclos");
        }
        obj.setDisplaySlot(DisplaySlot.SIDEBAR);

        // On récupère les counts
        Map<EntityType,Integer> counts = session.countAnimals();

        // On va clear les anciennes teams / entries
        for (String entry : sb.getEntries()) {
            sb.resetScores(entry);
        }

        // Titre
        // On peut paramétrer un score pour un "titre décoratif"
        String title = ChatColor.YELLOW + "== Enclos ==";
        obj.getScore(title).setScore(999);

        // Ajout de chaque ligne : ex. "Cows: 3"
        int line = 998;
        for (EntityType type : MAIN_SPECIES) {
            int c = counts.getOrDefault(type, 0);
            String lineStr = ChatColor.GREEN + type.name() + ": " + c;
            obj.getScore(lineStr).setScore(line--);
        }

        // Applique scoreboard
        p.setScoreboard(sb);
    }

    /* ============================================
     *      Classe interne RanchSession
     * ============================================ */
    private static final class RanchSession {
        private final JavaPlugin plugin;
        private final World world;
        private final int baseX, baseY, baseZ, width, length;

        private Villager rancher;
        private final List<Golem> golems = new ArrayList<>();
        private final List<Block> chestBlocks = new ArrayList<>();

        private BukkitRunnable ranchTask;

        private static final int CHESTS_PER_CORNER = 6;
        private static final Material FENCE_BLOCK  = Material.OAK_FENCE;
        private static final Material GROUND_BLOCK = Material.GRASS_BLOCK;

        // On place un mini-lampadaire tous les 4 blocs
        private static final int LAMP_SPACING = 4;

        RanchSession(JavaPlugin plugin, Location origin, int width, int length) {
            this.plugin = plugin;
            this.world  = origin.getWorld();
            this.baseX  = origin.getBlockX();
            this.baseY  = origin.getBlockY();
            this.baseZ  = origin.getBlockZ();
            this.width  = width;
            this.length = length;
        }

        public void start() {
            buildFence();
            buildGround();
            placeChests();
            placeSpawners();

            spawnOrRespawnRancher();
            spawnOrRespawnGolems();

            runRanchLoop();
        }

        public void stop() {
            if (ranchTask != null) {
                ranchTask.cancel();
                ranchTask = null;
            }
            // PNJ
            if (rancher != null && !rancher.isDead()) {
                rancher.remove();
            }
            // Golems
            for (Golem g : golems) {
                g.remove();
            }
            golems.clear();
        }

        /* --------- Test si location est dans l’enclos ---------- */
        public boolean isInside(Location loc) {
            if (loc.getWorld() == null) return false;
            if (!loc.getWorld().equals(world)) return false;
            int x = loc.getBlockX();
            int y = loc.getBlockY();
            int z = loc.getBlockZ();
            // On considère le "sol" + 10 blocs de hauteur
            if (x < baseX || x >= baseX + width) return false;
            if (z < baseZ || z >= baseZ + length) return false;
            if (y < baseY || y > baseY + 10) return false;
            return true;
        }

        /* --------- Construction de l’enclos ---------- */
        private void buildFence() {
            // On fait un « cadre » en fences
            // Tous les LAMP_SPACING blocs, on place un "mini-lampadaire" (2 fences + lanterne)
            for (int dx = 0; dx <= width; dx++) {
                buildFenceColumn(baseX + dx, baseZ);
                buildFenceColumn(baseX + dx, baseZ + length);
            }
            for (int dz = 0; dz <= length; dz++) {
                buildFenceColumn(baseX, baseZ + dz);
                buildFenceColumn(baseX + width, baseZ + dz);
            }

            // On met une fence gate pour l’entrée, par ex. au milieu du côté Nord
            int gateX = baseX + width / 2;
            world.getBlockAt(gateX, baseY + 1, baseZ).setType(Material.OAK_FENCE_GATE, false);
        }

        /**
         * Place un poteau de fence (1 ou 2 de hauteur) + éventuellement lanterne
         * si (x,z) correspond à un multiple de LAMP_SPACING
         */
        private void buildFenceColumn(int x, int z) {
            // On place au moins 1 bloc de fence
            setBlock(x, baseY + 1, z, FENCE_BLOCK);

            // Tous les LAMP_SPACING blocs, on met un mini-lampadaire
            boolean lampHere = ((x - baseX) % LAMP_SPACING == 0) && ((z - baseZ) % LAMP_SPACING == 0);

            if (lampHere) {
                // On ajoute un second bloc fence
                setBlock(x, baseY + 2, z, FENCE_BLOCK);
                // Puis la lanterne
                setBlock(x, baseY + 3, z, Material.LANTERN);
            }
        }

        private void buildGround() {
            // On remplace la surface par de l'herbe
            for (int dx = 0; dx < width; dx++) {
                for (int dz = 0; dz < length; dz++) {
                    setBlock(baseX + dx, baseY, baseZ + dz, GROUND_BLOCK);
                }
            }
        }

        private void placeChests() {
            // 4 coins : NW, NE, SW, SE
            createChests(new Location(world, baseX - 2,      baseY, baseZ - 2),       true);
            createChests(new Location(world, baseX + width + 1, baseY, baseZ - 2),    false);
            createChests(new Location(world, baseX - 2,      baseY, baseZ + length + 1), true);
            createChests(new Location(world, baseX + width + 1, baseY, baseZ + length + 1), false);
        }

        private void createChests(Location start, boolean positiveX) {
            for (int i = 0; i < CHESTS_PER_CORNER; i++) {
                Location loc = start.clone().add(positiveX ? i : -i, 0, 0);
                setBlock(loc, Material.CHEST);
                chestBlocks.add(loc.getBlock());
            }
        }

        private void placeSpawners() {
            // 1 spawner par espèce => en ligne
            int offsetZ = baseZ + 2;
            int currentX = baseX + 2;
            for (EntityType type : MAIN_SPECIES) {
                Location spawnerLoc = new Location(world, currentX, baseY + 1, offsetZ);
                if (spawnerLoc.getBlockX() >= baseX + width - 1) break; // plus de place
                setBlock(spawnerLoc, Material.SPAWNER);

                Block spawnerBlock = spawnerLoc.getBlock();
                if (spawnerBlock.getState() instanceof CreatureSpawner cs) {
                    cs.setSpawnedType(type);
                    cs.update();
                }
                currentX += 4; // on avance de 4 blocs
            }
        }

        /* --------- PNJ + golems ---------- */
        private void spawnOrRespawnRancher() {
            if (rancher != null && !rancher.isDead()) return;
            Location center = new Location(world,
                    baseX + width / 2.0,
                    baseY + 1,
                    baseZ + length / 2.0);
            rancher = (Villager) world.spawnEntity(center, EntityType.VILLAGER);
            rancher.setCustomName("Éleveur");
            rancher.setCustomNameVisible(true);
            rancher.setProfession(Villager.Profession.BUTCHER);
            rancher.setVillagerLevel(5); // Niveau max

            // On configure ses trades
            setupTrades(rancher);
        }

        /**
         * Crée 8 recettes :
         *   - 64 [BEEF|PORKCHOP|CHICKEN|MUTTON] => 1 diamond
         *   - 64 [COOKED_BEEF|COOKED_PORKCHOP|COOKED_CHICKEN|COOKED_MUTTON] => 2 diamonds
         */
        private void setupTrades(Villager v) {
            List<MerchantRecipe> recipes = new ArrayList<>();

            // Matières premières crues
            recipes.add(createRecipe(Material.BEEF, 64, new ItemStack(Material.DIAMOND, 1)));
            recipes.add(createRecipe(Material.PORKCHOP, 64, new ItemStack(Material.DIAMOND, 1)));
            recipes.add(createRecipe(Material.CHICKEN, 64, new ItemStack(Material.DIAMOND, 1)));
            recipes.add(createRecipe(Material.MUTTON, 64, new ItemStack(Material.DIAMOND, 1)));

            // Viandes cuites
            recipes.add(createRecipe(Material.COOKED_BEEF, 64, new ItemStack(Material.DIAMOND, 2)));
            recipes.add(createRecipe(Material.COOKED_PORKCHOP, 64, new ItemStack(Material.DIAMOND, 2)));
            recipes.add(createRecipe(Material.COOKED_CHICKEN, 64, new ItemStack(Material.DIAMOND, 2)));
            recipes.add(createRecipe(Material.COOKED_MUTTON, 64, new ItemStack(Material.DIAMOND, 2)));

            v.setRecipes(recipes);
        }

        /**
         * Crée une MerchantRecipe de la forme :
         *   - 64 "matInput" => "output"
         * Recette illimitée (maxUses très grand).
         */
        private MerchantRecipe createRecipe(Material matInput, int amount, ItemStack output) {
            MerchantRecipe recipe = new MerchantRecipe(output, 9999999); // maxUses
            recipe.addIngredient(new ItemStack(matInput, amount));
            recipe.setExperienceReward(false);
            return recipe;
        }

        private void spawnOrRespawnGolems() {
            golems.removeIf(g -> g.getGolem().isDead());
            while (golems.size() < 2) {
                Location c = new Location(world,
                        baseX + width / 2.0,
                        baseY,
                        baseZ + length / 2.0).add(golems.size()*2.0 - 1.0, 0, -1.5);

                double radius = Math.max(1.0,
                        Math.min(width, length) / 2.0 - 1.0);

                Golem g = new Golem(plugin, c, radius);
                g.getGolem().setCustomName("Golem Éleveur");
                golems.add(g);
            }
        }

        /* --------- Boucle de vérification ---------- */
        private void runRanchLoop() {
            ranchTask = new BukkitRunnable() {
                @Override
                public void run() {
                    if (rancher == null || rancher.isDead()) {
                        spawnOrRespawnRancher();
                    }
                    spawnOrRespawnGolems();

                    // On analyse la zone, pour chaque espèce
                    for (EntityType type : MAIN_SPECIES) {
                        cullExcessAnimals(type);
                    }
                }
            };
            ranchTask.runTaskTimer(plugin, 20L, 20L); // 1 fois par seconde
        }

        /**
         * Repère tous les animaux "type" dans la zone.
         * Si plus de 5, on tue l'excès et on génère le loot correspondant (5% chance cuit).
         */
        private void cullExcessAnimals(EntityType type) {
            List<LivingEntity> inZone = getEntitiesInZone(type);
            int count = inZone.size();
            int limit = 5;
            if (count <= limit) return; // tout va bien
            int toRemove = count - limit;

            Random rand = new Random();

            for (int i = 0; i < toRemove; i++) {
                if (inZone.isEmpty()) break;
                LivingEntity victim = inZone.remove(inZone.size() - 1); // prend la dernière
                // Téléportation du PNJ au-dessus, "pour le show"
                if (rancher != null && !rancher.isDead()) {
                    rancher.teleport(victim.getLocation().add(0.5, 1, 0.5));
                }

                // On simule le drop
                List<ItemStack> drops = simulateLoot(type, rand);

                // On enlève la bête
                victim.remove();

                // On dépose les loots
                deposit(drops);
            }
        }

        /**
         * Calcule le nombre d’animaux par espèce dans l’enclos
         * (pour le scoreboard).
         */
        public Map<EntityType,Integer> countAnimals() {
            Map<EntityType,Integer> map = new HashMap<>();
            for (EntityType et : MAIN_SPECIES) {
                map.put(et, getEntitiesInZone(et).size());
            }
            return map;
        }

        private List<LivingEntity> getEntitiesInZone(EntityType type) {
            List<LivingEntity> result = new ArrayList<>();
            int minX = baseX, maxX = baseX + width - 1;
            int minZ = baseZ, maxZ = baseZ + length - 1;
            int minY = baseY, maxY = baseY + 10; // on limite la hauteur
            for (Entity e : world.getEntities()) {
                if (e.getType() == type && e instanceof LivingEntity le) {
                    Location loc = e.getLocation();
                    int x = loc.getBlockX();
                    int y = loc.getBlockY();
                    int z = loc.getBlockZ();
                    if (x >= minX && x <= maxX
                            && z >= minZ && z <= maxZ
                            && y >= minY && y <= maxY) {
                        result.add(le);
                    }
                }
            }
            return result;
        }

        /**
         * Génère des loots basés sur l’espèce, avec 5 % de chance de viande cuite.
         */
        private List<ItemStack> simulateLoot(EntityType type, Random rand) {
            List<ItemStack> loot = new ArrayList<>();
            switch (type) {
                case COW -> {
                    // Viande (1 à 3) + cuir (0 à 2)
                    int beef = 1 + rand.nextInt(3);
                    int leather = rand.nextInt(3);
                    // 5% cuit
                    Material rawBeef = (rand.nextInt(100) < 5) ? Material.COOKED_BEEF : Material.BEEF;
                    loot.add(new ItemStack(rawBeef, beef));
                    if (leather > 0) {
                        loot.add(new ItemStack(Material.LEATHER, leather));
                    }
                }
                case CHICKEN -> {
                    // 1 à 2 raw chicken + 0 à 2 plumes
                    int chicken = 1 + rand.nextInt(2);
                    int feather = rand.nextInt(3);
                    Material rawChicken = (rand.nextInt(100) < 5) ? Material.COOKED_CHICKEN : Material.CHICKEN;
                    loot.add(new ItemStack(rawChicken, chicken));
                    if (feather > 0) {
                        loot.add(new ItemStack(Material.FEATHER, feather));
                    }
                }
                case PIG -> {
                    // 1 à 3 porc cru
                    int pork = 1 + rand.nextInt(3);
                    Material rawPork = (rand.nextInt(100) < 5) ? Material.COOKED_PORKCHOP : Material.PORKCHOP;
                    loot.add(new ItemStack(rawPork, pork));
                }
                case SHEEP -> {
                    // 1 à 2 mutton + 1 wool
                    int mutton = 1 + rand.nextInt(2);
                    Material rawMutton = (rand.nextInt(100) < 5) ? Material.COOKED_MUTTON : Material.MUTTON;
                    loot.add(new ItemStack(rawMutton, mutton));
                    loot.add(new ItemStack(Material.WHITE_WOOL, 1));
                }
                default -> {
                    // rien
                }
            }
            return loot;
        }

        private void deposit(List<ItemStack> items) {
            if (items.isEmpty() || chestBlocks.isEmpty()) return;
            // round-robin "au hasard" => on pioche un coffre
            Block chestBlock = chestBlocks.get(new Random().nextInt(chestBlocks.size()));
            if (chestBlock.getType() == Material.CHEST) {
                Chest c = (Chest) chestBlock.getState();
                Inventory inv = c.getInventory();
                for (ItemStack drop : items) {
                    inv.addItem(drop);
                }
            }
        }

        /* --------- API public : coffres cassés, etc. --------- */
        public boolean isChestBlock(Block b) {
            return chestBlocks.contains(b);
        }
        public void removeChest(Block b) {
            chestBlocks.remove(b);
        }
        public boolean hasChests() {
            return !chestBlocks.isEmpty();
        }

        /* --------- Persistance --------- */
        public Map<String, Object> toMap() {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("world", world.getUID().toString());
            map.put("x", baseX);
            map.put("y", baseY);
            map.put("z", baseZ);
            map.put("width", width);
            map.put("length", length);
            return map;
        }

        /* --------- Outil setBlock --------- */
        private void setBlock(int x, int y, int z, Material mat) {
            world.getBlockAt(x, y, z).setType(mat, false);
        }
        private void setBlock(Location loc, Material mat) {
            setBlock(loc.getBlockX(), loc.getBlockY(), loc.getBlockZ(), mat);
        }
    }

    /* ============================================
     *   Classe interne Selection (2 coins)
     * ============================================ */
    private static class Selection {
        private Block corner1;
        private Block corner2;
    }
}
