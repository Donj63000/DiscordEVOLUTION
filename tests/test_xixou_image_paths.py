"""Je vérifie les dossiers d'images et le refus des adresses hors catalogue."""

import pytest

from utils.xixou_image_paths import is_allowed_image_url, xixou_image_candidates


@pytest.mark.parametrize("family,category,image,expected", [
    ("equipements", "anneaux", "47.svg", "xixou-anneaux/47.png"),
    ("ressources", "laine", "56.svg", "xixou-ressources/images_laine/56.png"),
    ("ressources", "bois", "17.svg", "xixou-ressources/images_bois/17.png"),
    ("ressources", "minerai", "108.svg", "xixou-ressources/images_minerai/108.png"),
])
def test_verified_item_images_use_public_png_counterpart(family, category, image, expected):
    result = xixou_image_candidates(family, category, image)
    assert result == ("https://xixou.io/wp-content/uploads/xixou-og/" + expected,)
    assert is_allowed_image_url(result[0])


@pytest.mark.parametrize("category,folder", [
    ("certificat-chenil", "images_certificat_chenils"),
    ("certificat-monture", "images_certificat_montures"),
    ("maitrise", "images_maitrises"),
    ("parchemin-caracteristique", "images_parchemin_caracteristiques"),
    ("parchemin-experience", "images_parchemin_experiences"),
    ("parchemin-sort", "images_parchemin_sorts"),
    ("potion-forgemagie", "images_potion-de-forgemagie"),
    ("potion-de-forgemagie", "images_potion-de-forgemagie"),
    ("ecailles-dragon-133", "images_ecailles"),
    ("carte-ttg", "tcg-images/cartes"),
    ("fantome-de-familier", "images_fantome-de-familier"),
    ("pierre-brute", "images_pierre-brute"),
    ("viande-conservee", "images_viande-conservee"),
])
def test_verified_resource_folder_exceptions_keep_original_spelling(category, folder):
    assert xixou_image_candidates("ressources", category, "1.svg") == (
        f"https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/{folder}/1.png",
    )


def test_equipment_bag_folder_is_not_guessed_from_category():
    assert xixou_image_candidates("equipements", "sacs", "7.svg") == (
        "https://xixou.io/wp-content/uploads/xixou-og/xixou-sacados/7.png",
    )


def test_same_image_basename_in_different_categories_keeps_distinct_identity():
    ring = xixou_image_candidates("equipements", "anneaux", "47.svg")
    stone = xixou_image_candidates("ressources", "pierre-brute", "47.svg")
    assert ring != stone
    assert "xixou-anneaux" in ring[0]
    assert "images_pierre-brute" in stone[0]


@pytest.mark.parametrize("category", ["paquets", "paquet-de-cartes"])
def test_existing_png_with_nonnumeric_filename_is_first_candidate(category):
    result = xixou_image_candidates("ressources", category, "communes-v2.png")
    assert result == (
        "https://xixou.io/wp-content/uploads/xixou-ressources/tcg-images/paquets/communes-v2.png",
        "https://xixou.io/wp-content/uploads/xixou-og/"
        "xixou-ressources/tcg-images/paquets/communes-v2.png",
    )
    assert all(is_allowed_image_url(url) for url in result)


@pytest.mark.parametrize("family,category", [
    ("ressources", "arcs"), ("equipements", "bois"), ("monstres", "monstres"),
    ("equipement", "anneaux"), ("equipements", "inconnue"),
    ("equipements", "../anneaux"), ("ressources", "images_bois"),
    ("equipements", "dragodindes"), ("equipements", "panoplies"),
    ("equipements", "pierres"), ("equipements", "ecailles"),
    (None, "anneaux"), ([], "anneaux"), ("ressources", {}),
])
def test_unknown_or_unverified_family_and_category_do_not_invent_image_urls(family, category):
    assert xixou_image_candidates(family, category, "47.svg") == ()


@pytest.mark.parametrize("filename", [
    None, 47, [], {}, "", "/47.svg", "../47.svg", "..\\47.svg", "folder/47.svg",
    "47.svg?token=hidden", "47.svg#fragment", "47.svg?", "47.svg#", "47.svg\n",
    "47.svg%00", "%2e%2e%2f47.svg", "47%2esvg", "https://untrusted.test/47.svg",
    "//untrusted.test/47.svg", "47.svg.png", "47.jpg", "47.webp", "47.SVG", " 47.svg",
    "47.svg ", "47.svg\x00", "équipement.svg", "a" * 129 + ".svg",
])
def test_malformed_api_image_name_is_not_a_download_target(filename):
    assert xixou_image_candidates("equipements", "anneaux", filename) == ()


@pytest.mark.parametrize("url", [
    "https://wiki.moon-bot.io/icons/item_9_47.png",
    "https://wiki.moon-bot.io/icons/gelano.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png",
    "https://xixou.io/wp-content/uploads/xixou-anneaux/47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/images_bois/17.png",
    "https://xixou.io/wp-content/uploads/xixou-ressources/tcg-images/paquets/communes-v2.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/tcg-images/cartes/15001-v2.png",
])
def test_allowed_png_hosts_and_verified_folders(url):
    assert is_allowed_image_url(url)


@pytest.mark.parametrize("url", [
    None, 47, [], {}, "", "https://untrusted.test/icons/item_9_47.png",
    "http://wiki.moon-bot.io/icons/item_9_47.png", "//wiki.moon-bot.io/icons/item_9_47.png",
    "https://wiki.moon-bot.io@untrusted.test/icons/item_9_47.png",
    "https://wiki.moon-bot.io.untrusted.test/icons/item_9_47.png",
    "https://wiki.moon-bot.io:443/icons/item_9_47.png",
    "https://user@wiki.moon-bot.io/icons/item_9_47.png",
    "https://wiki.moon-bot.io/icons/item_9_47.png?key=hidden",
    "https://wiki.moon-bot.io/icons/item_9_47.png#fragment",
    "https://wiki.moon-bot.io/icons/item_9_47.png?",
    "https://wiki.moon-bot.io/icons/item_9_47.png#",
    "https://wiki.moon-bot.io/icons/item_9_47.png\n",
    "https://wiki.moon-bot.io/icons/item_9_47.webp",
    "https://wiki.moon-bot.io/icons/../icons/item_9_47.png",
    "https://wiki.moon-bot.io/icons/%2e%2e/icons/item_9_47.png",
    "https://wiki.moon-bot.io/icons/item%5f9%5f47.png",
    "https://wiki.moon-bot.io/icons/item_9_47.png/other.png",
    "https://xixou.io@untrusted.test/wp-content/uploads/xixou-og/xixou-anneaux/47.png",
    "https://xixou.io.untrusted.test/wp-content/uploads/xixou-og/xixou-anneaux/47.png",
    "https://xixou.io:443/wp-content/uploads/xixou-og/xixou-anneaux/47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.svg",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png?secret=hidden",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png#fragment",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png?",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png#",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png\x00",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/../xixou-capes/47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/%2e%2e/47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux%2f47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47%2epng",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux\\47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-inconnue/47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/images_../47.png",
    "https://xixou.io/wp-content/uploads/private/47.png",
    "https://xixou.io/wp-content/uploads/xixou-og/xixou-og/xixou-anneaux/47.png",
    "https://xixou.io/wp-admin/47.png",
])
def test_urls_with_untrusted_authority_path_encoding_or_suffix_are_rejected(url):
    assert not is_allowed_image_url(url)
