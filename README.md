# Nilan VP18c / CTS600 pro Home Assistant

[![Validate](https://github.com/honasek/Nilan-VP18c/actions/workflows/validate.yml/badge.svg)](https://github.com/honasek/Nilan-VP18c/actions/workflows/validate.yml)

Custom integrace řídicího systému **Nilan CTS600** s připojením přes transparentní TCP–RS485 bránu. Tato varianta vychází z [frodef/nilan-cts600-homeassistant](https://github.com/frodef/nilan-cts600-homeassistant) a obsahuje rozšíření vyvíjená a provozovaná na Nilan VP18 K.

Aktuální distribuční verze: **2026.10.19.3**. Provozní kód odpovídá verzi 2026.10.19.2 ověřené na Home Assistant Core **2026.10.2**; vydání .3 upravuje balení a metadata pro HACS. Kompatibilita konkrétních položek menu závisí na jednotce a jejím firmwaru; název repozitáře není potvrzením otestování všech variant VP18.

## Funkce

- Klimatická entita: provozní režim, cílová teplota a stupně ventilace.
- Předvolby Úsporný / Komfortní / Více teplé vody podle dostupného menu.
- Teploty, displej, provozní stav a alarmy CTS600.
- Ovládání podporovaných položek uživatelského a servisního menu, hodin a týdenního programu.
- Samostatné entity TUV: kompresor s teplotou T12 a spirála s teplotou T11.
- Lokální evidence údržby filtru s datem poslední údržby.
- Odhad tepelné zásoby TUV, krátkodobého výhledu a pasivní simulace plánování.
- Primární a záložní pokojový senzor T15, kontrola platnosti teploty a samostatné povolení ovládání.

Model TUV je odhad ze senzorů a naučeného průběhu. Pasivní simulace plánování sama nepřepíná zařízení. Kompletní osobní automatizace, energetický plánovač a dashboardy autora nejsou součástí této integrace.

## Instalace přes HACS

1. V HACS otevřete nabídku **⋮ → Vlastní repozitáře / Custom repositories**.
2. Přidejte `https://github.com/honasek/Nilan-VP18c`, kategorii **Integration**.
3. Stáhněte **Nilan VP18c / CTS600** a restartujte Home Assistant.
4. Připravte pokojový senzor a pomocníka pro povolení ovládání podle následující části.
5. Otevřete **Nastavení → Zařízení a služby → Přidat integraci → Nilan CTS600**.

Repozitář lze přidat ručně; toto není tvrzení o jeho zařazení do výchozího katalogu HACS.

V nabídce stažení vyberte vydanou verzi. HACS instaluje soubory přímo z adresáře `custom_components/nilan_cts600` příslušného GitHub vydání; ruční rozbalování ani vytváření další vnořené složky není potřeba. Přehled vydání je na stránce [Releases](https://github.com/honasek/Nilan-VP18c/releases).

Při ruční instalaci zkopírujte adresář `custom_components/nilan_cts600` do stejné cesty v konfiguraci HA a restartujte HA. Doména je `nilan_cts600`, proto se tato varianta instaluje na místo dosavadní integrace stejné domény.

## Připojení a první nastavení

Integrace nahrazuje panel CTS600 a používá jeho komunikační protokol. Nejde o běžnou integraci CTS602 přes Modbus TCP. Použijte transparentní TCP přenos, na bráně **19200 baud, 8N2, Protocol NONE**. Formulář vyžaduje potvrzení odpojení původního panelu a ověření zapojení CN6.

Při vytvoření konfigurace se spouští emulace panelu, posílá se pokojová teplota T15 a menu se přepíná do angličtiny. Vypnutý pomocník pro ovládání blokuje uživatelské ovládací příkazy, ale není vypínačem komunikace ani emulace panelu.

Ve formuláři vyberte:

- IP adresu nebo hostname brány a její TCP port; předvyplněný port je 4196, skutečná hodnota musí odpovídat bráně.
- Primární, případně záložní pokojový teplotní senzor T15. Musí poskytovat čerstvou číselnou teplotu v °C v rozsahu 5–40 °C.
- Existující pomocník typu Přepínač (`input_boolean`) pro povolení ovládání. Lze jej vytvořit v **Nastavení → Zařízení a služby → Pomocníci**.

Předvyplněné entity pokojových senzorů jsou původní hodnoty z instalace autora. Při nové instalaci je změňte na vlastní senzory.

Výchozí maximální stáří T15 je 15 minut. Pokud existuje `input_number.nilan_t15_max_age_minutes`, používá se jeho hodnota 1–60 minut. Chybějící nebo neplatná teplota blokuje ovládání.

## Další části a uchování dat

Příkazy `set_clock`, `set_weekly_slot`, `discover_menu_options` a `acknowledge_alarm` jsou dostupné v doméně `nilan_cts600`; popis polí je v [services.yaml](custom_components/nilan_cts600/services.yaml).

Změny menu se provádějí přes serializované operace a ověřují zpětným čtením. Dostupnost konkrétní položky může být omezena firmwarem nebo servisním povolením.

Datum údržby filtru, model TUV a naučená data se ukládají do úložiště Home Assistantu. Uložení data údržby neposílá příkaz jednotce a nepotvrzuje její alarm. Při aktualizaci kódu tyto uložené údaje nemažte.

Model a simulace mohou používat pomocníky a měřicí entity z konkrétní domácí konfigurace. Tento repozitář neobsahuje osobní `configuration.yaml`, packages, historii měření ani Lovelace. Jejich přenos vyžaduje vlastní nastavení entit; samotná instalace přes HACS je nevytváří.

## Vývoj a ověření

Distribuční struktura odpovídá kategorii HACS **Integration**:

```text
hacs.json
README.md
LICENSE
custom_components/
  nilan_cts600/
    manifest.json
    __init__.py
    brand/
    translations/
    …ostatní moduly a services.yaml…
docs/             # historická dokumentace; neinstaluje se do HA
script/           # kontrola repozitáře
tests/            # testy; neinstalují se do HA
.github/workflows/validate.yml
```

`content_in_root` a `zip_release` jsou vypnuté: používá se standardní adresář integrace v repozitáři, bez samostatného instalačního ZIP souboru.

```sh
python -m pip install -r requirements-test.txt
python script/validate_repository.py
python -m pytest tests -q
python -m compileall -q custom_components
```

Přiložené přenositelné testy ověřují tepelný model bez připojení k fyzické jednotce. Nejde o úplný test hardwaru nebo všech ovládacích cest.

Při každém pushi a pull requestu běží oficiální **HACS Action**, **Home Assistant Hassfest** a kontrola struktury s testy modelu. Před vydáním musí projít všechny kontroly; tag vydání musí odpovídat `version` v `manifest.json`. Změny jednotlivých verzí jsou v [CHANGELOG.md](CHANGELOG.md).

## Původ a licence

Původní autor protokolu a integrace: **frodef**. Původní projekt: [nilan-cts600-homeassistant](https://github.com/frodef/nilan-cts600-homeassistant).

Původní licenci **The Unlicense** zachovává [LICENSE](LICENSE). Historický [UPSTREAM_README.md](docs/UPSTREAM_README.md) je přiložen kvůli původu projektu; jeho postup pro původní variantu nenahrazuje návod výše pro tuto TCP variantu.
