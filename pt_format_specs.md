# Spécifications binaires du format Pro Tools (`.ptx`)

*(Spécification du code courant de `pt_api`, version déclarée 1.6.1, publication en préparation. Le correctif de cadences exactes (§1.4) a passé la validation finale dans Pro Tools et les contrôles des sauvegardes natives. Les checkpoints et statuts historiques de l'annexe gardent leur contexte d'origine; ils ne remplacent pas les règles courantes. Les profils exclus/reportés ne deviennent pas pris en charge par un changement de version.)*

Ce document décrit exactement les structures que le code courant lit, valide, modifie et sérialise. Une structure dite « observée » provient des sessions de référence; une structure dite « prise en charge » possède un chemin explicite dans `pt_api.py`. Les zones non interprétées sont conservées telles quelles et ne doivent pas être déduites par heuristique.

## 0. Portée et conventions de lecture

Les sections 1 à 12 décrivent les structures et algorithmes applicables aux **profils binaires pris en charge**, indépendamment du basename PTX, des noms de pistes/clips/groupes et des conventions d'une application cliente. Elles ne constituent pas une spécification universelle de toutes les révisions PTX. Un autre nom ne change pas un profil; un autre layout peut le rendre non pris en charge.

Les noms fournis à l'API sont des données : leur validation UTF-8/NUL, unicité, comparaison exacte, relations entre miroirs et éventuelles contraintes de longueur restent celles de chaque méthode. `name_len` désigne une longueur en octets encodés, pas un nombre de caractères. Les identités, ordinaux, codes, timestamps et compteurs doivent être résolus depuis la session courante selon le profil; ne pas utiliser les valeurs d'un fichier de référence comme constantes.

Sauf mention contraire, `+n` est relatif au payload du bloc cité, pas à l'offset absolu du fichier. `A = 4 + name_len` désigne la fin du nom préfixé UInt32 d'un clip/groupe. `T` désigne le début de sa queue après le nom dans les tableaux qui le définissent. Ces offsets se calculent depuis la longueur du nom courant : aucun nom de fixture ni alignement implicite n'intervient. `N`, `K`, `i`, `ordinal` et les autres variables sont locaux à la structure décrite.

Distinguer **constantes du profil** (magic, flags, tailles fixes, headers exacts), **données variables de session** (noms, IDs, nombres de records, positions, gains) et **observations de validation** (valeurs concrètes du corpus). Une taille/arité explicitement exigée par le code ne devient pas variable au motif que les noms sont libres. La chaîne `Audio Files` reste un libellé/convention réellement utilisé par les profils média et les helpers de fichiers; ce n'est pas un nom de piste imposé. Les libellés opaques internes historiques, par exemple `Audio N`, sont préservés sans dicter le nom visible d'une piste.

L'annexe A rassemble les exemples et preuves de validation, séparés des règles. Leurs noms, timecodes, compteurs, codes et nombres de tests ne sont **jamais** des préconditions générales. Les mentions historiques de statut ou « suite courante » y décrivent le point de contrôle concerné; le statut applicable est celui des sections normatives. Le catalogue des erreurs (§13) reste à la fin du document. L'historique de travail et les prochaines actions appartiennent au handoff, pas aux algorithmes.

**Politique de généralisation** : la validation porte sur les invariants d'un schéma établi, et non sur une liste de commandes ou valeurs ayant chacune une fixture. Une variation de données dans les bornes du schéma n'exige pas une nouvelle référence native. Un nouveau layout, une croissance de largeur non définie, une ambiguïté de liens ou une nouvelle mutation de champ opaque exige au contraire une preuve spécifique. Une composition de schémas existants peut être acceptée si les dépendances restent corroborées et si les données non ciblées sont préservées; elle ne vaut pas validation native universelle. Aucun mode permissif/force n'ignore les erreurs structurelles.

**Provenance et licence** : ptformat (https://github.com/zamaudio/ptformat), dont la source crédite Damien Zammit et Robin Gareus, a servi de référence et d'inspiration initiale pour comprendre le format et son déchiffrement. La distribution source courante de pt_api est sous **LGPL-2.1-or-later** depuis la correction de notices du 7 octobre 2026; voir LICENSE, COPYING et NOTICE.md. Cette mention n'attribue pas chaque offset ou découverte documentée à ptformat, ne prétend pas établir une copie ligne par ligne et ne remplace pas la revue détaillée de provenance. Aucun comportement, champ binaire ou contrat d'erreur ne change avec cette correction de licence.

## 1. Enveloppe du fichier, chiffrement et parsing

### 1.1 Organisation générale

```text
[En-tête non chiffré de 20 octets]
[Bloc spécial 0x0001]
[Blocs et segments racines]
[Bloc racine final 0x0002]
```

L'en-tête occupe les offsets `0x00..0x13` :

| Offset | Taille | Valeur prise en charge |
|---|---:|---|
| `+0x00` | 1 | Signature `0x03`. |
| `+0x01` | 16 | Champ de version composé uniquement des octets ASCII `0` et `1`. |
| `+0x11` | 1 | Endianness : `0x00` little-endian, `0x01` big-endian. L'enveloppe accepte les deux valeurs, mais `ProToolsSession` rejette `0x01` avant le parsing des payloads métier. |
| `+0x12` | 1 | Mode XOR : `0x01` ou `0x05`. |
| `+0x13` | 1 | Valeur XOR servant à retrouver le delta. |

Un fichier de moins de 20 octets, une signature/version invalide, un drapeau d'endianness inconnu ou un mode XOR autre que `0x01`/`0x05` est rejeté avant l'analyse du corps.

### 1.2 Transformation XOR

Le chiffrement et le déchiffrement utilisent la même transformation symétrique et ne modifient jamais les 20 octets d'en-tête.

- `gen_xor_delta()` recherche le delta par balayage des 256 valeurs possibles.
- Mode `0x01` : le delta est l'entier `i` tel que `(i × 53) & 0xFF == xor_value`; l'index de clé est `offset & 0xFF`.
- Mode `0x05` : le delta est l'opposé modulo 256 de l'entier `i` tel que `(i × 11) & 0xFF == xor_value`; l'index de clé est `(offset >> 12) & 0xFF`.
- La table de clé contient `((index × delta) & 0xFF)` pour les 256 index.
- Chaque octet du corps, à partir de `0x14`, est XORé avec l'entrée correspondante.
- `unxor_session()` retourne une nouvelle `bytearray` déchiffrée. `xor_session()` transforme aussi une copie; le tampon fourni par l'appelant n'est pas muté.
- `xor_session()` écrit dans un fichier temporaire du dossier cible, vérifie les écritures courtes, puis installe la destination avec `os.replace()`; son retour normal est `None`.

### 1.3 Format générique d'un bloc

```text
0x5A | block_type UInt16 | block_size UInt32 | [content_type UInt16 | payload]
```

Tous les champs suivent théoriquement l'endianness de la session. Les payloads métier de l'API sont cependant définis uniquement en little-endian.

- `block_size` compte les deux octets de `content_type` et le payload, mais pas les sept premiers octets de l'en-tête.
- Un bloc vide possède `block_size = 0`, aucun `content_type` physique et une taille totale de 7 octets.
- `block_size = 1` est impossible : un bloc non vide doit contenir au moins le `content_type` de deux octets.
- Le champ physique `block_type` est UInt16. Le parseur ne reconnaît comme bloc que les valeurs `0x0000..0x00FF`; un octet haut non nul fait traiter la séquence comme donnée brute. `PTBlock.to_bytes()` applique la même borne 8-bit.
- `content_type` doit tenir sur UInt16; tailles et offsets sérialisés doivent tenir sur UInt32.
- Un `PTBlock` contient uniquement des `bytes`, `bytearray` ou d'autres `PTBlock`.
- `original_offset > 0` identifie un bloc chargé et doit être unique dans tout l'arbre. Les nouveaux blocs utilisent `-1` ou `0`.
- Parsing, sérialisation et `get_all_blocks()` sont limités à 128 niveaux et rejettent les cycles.

Les helpers `u_endian_read2(buf, offset, is_bigendian)` et `u_endian_read4(...)` lisent respectivement un UInt16 et un UInt32 selon l'endianness demandée. Ils restent des utilitaires d'implémentation, pas une abstraction de payload métier big-endian.

Pour tout type non plat, le parseur tente récursivement toute séquence `0x5A` dont l'en-tête, la taille et le `block_type` sont plausibles dans la borne du parent, sauf dans un préfixe brut explicitement défini. Les types à payload fixe suivants sont toujours conservés à plat, même si leurs données contiennent un faux en-tête plausible :

```text
0x0002  0x1028  0x104f  0x204d  0x260a  0x262f  0x2637
```

Cette liste est une règle de sécurité : promouvoir un timestamp, un gain ou un pointeur contenant fortuitement `0x5A` en faux enfant peut amputer les données ou fausser les relocalisations.

**Préfixe brut de map, changement non publié** : `RAW_PREFIX_PTX_CONTENT_TYPES` définit uniquement `0x1054: 4`. Pour chaque map principale ou imbriquée, ses quatre premiers octets sont conservés comme compteur UInt32 avant toute recherche d'enfant. Une valeur de compteur contenant `0x5A` n'est donc pas interprétée comme un début de bloc. Aucun compteur n'est corrigé automatiquement. Un préfixe tronqué reste brut lors du parsing, puis est refusé par le validateur de playlists. Les autres conteneurs ne reçoivent pas cette règle par inférence. Validation du profil : annexe A (§1.3).

### 1.4 Métadonnées temporelles et `TimecodeEngine`

**État : intégré au code courant1.6.1, validation finale de cette passe terminée; publication en préparation.** Cette section décrit le code courant, pas seulement la release antérieure1.6.0.

- `0x1028` : exactement une racine est requise. Son premier segment brut doit mesurer au moins 6 octets; la fréquence d'échantillonnage est le UInt32 à `+2` et doit être positive.
- `0x204d` : exactement une racine est requise; son premier segment brut doit mesurer **au moins 44 octets**. La cadence exacte est le **UInt32 little-endian à `+40`**, exposé dans `session.frame_rate_enum`. Le premier octet à `+0` représente une famille ambiguë et n'est ni la source de cadence ni un fallback.
- Le payload reste plat et intégralement conservé. Les longueurs natives 67/68 octets ne sont pas une contrainte générale. Le champ observé à `+48`, les octets inconnus et la chaîne finale ne sont pas interprétés ou réécrits. Aucune méthode de changement de cadence PTX n'est ajoutée.
- Une valeur UInt32 inconnue peut être chargée et sauvegardée sans conversion. Elle n'est ni tronquée à un octet ni remplacée par sa famille; toute méthode demandant un calcul temporel la rejette.

| Enum exact | Cadence réelle (images/s) | Nominal `N` | Drop Frame / numéros omis `D` |
|---|---:|---:|---:|
| `0x01` | 24 | 24 | non / 0 |
| `0x02` | 25 | 25 | non / 0 |
| `0x03` | 30000/1001 | 30 | non / 0 |
| `0x05` | 30000/1001 | 30 | oui / 2 |
| `0x07` | 30 | 30 | non / 0 |
| `0x08` | 30 | 30 | oui / 2 |
| `0x09` | 24000/1001 | 24 | non / 0 |
| `0x0A` | 50 | 50 | non / 0 |
| `0x0B` | 60000/1001 | 60 | non / 0 |
| `0x0C` | 60000/1001 | 60 | oui / 4 |
| `0x0D` | 60 | 60 | non / 0 |
| `0x0E` | 60 | 60 | oui / 4 |
| `0x0F` | 48000/1001 | 48 | non / 0 |
| `0x10` | 48 | 48 | non / 0 |
| `0x11` | 100 | 100 | non / 0 |
| `0x12` | 120000/1001 | 120 | non / 0 |
| `0x13` | 120000/1001 | 120 | oui / 8 |
| `0x14` | 120 | 120 | non / 0 |
| `0x15` | 120 | 120 | oui / 8 |

`get_frame_rate()` conserve le contrat `(fps_float, is_drop_frame)`. Les rapports entiers exacts sont conservés séparément dans la table interne pour les conversions. `N = ceil(fps)`; pour les variantes DF, `D = 2*(N//30)`. Les variantes DF entières ne sont pas assimilées aux variantes à dénominateur 1001.

**Conversion position → échantillons** : les composants sont des entiers non booléens; heures non négatives, minutes/secondes dans 0..59, `0 <= FF < N`. En DF, à seconde zéro d'une minute non divisible par dix, `FF < D` est inexistant et refusé. Pour `M = 60*HH + MM`, le nombre d'images physiques est :

```text
F = (60*M + SS)*N + FF - D*(M - M//10)
samples = arrondi_demi_supérieur(F * sample_rate / fps_réel)
```

En non-drop, `D = 0`. Pour une **durée**, `duration_to_samples()` utilise `(3600*HH + 60*MM + SS)*N + FF`, sans retrancher de labels ni interdire les numéros omis. Position DF et durée ne sont pas interchangeables.

**Conversion échantillons → position** : le nombre entier d'échantillons doit être non négatif. Arrondir `samples * fps_réel / sample_rate` à l'image physique la plus proche. En DF, pour ce nombre `F`, définir `Q = 600*N - 9*D`, `P = 60*N-D`, `q,r = divmod(F,Q)`; ajouter `9*D*q` à `F`, puis `D*((r-D)//P)` si `r >= D`. Décomposer le nombre de labels obtenu en HH/MM/SS/FF au nominal `N`. Aucun bouclage implicite à 24 heures.

Les conversions utilisent des produits/quotients entiers et un arrondi demi-supérieur exact `(2*numérateur + dénominateur)//(2*dénominateur)`, évitant les erreurs d'une unité dues aux flottants aux longues positions. Un sample rate flottant accepté par le constructeur est traité selon son rapport binaire exact. Les résultats non représentables dans la plage numérique contrôlée sont refusés. Le texte public reste **`HH:MM:SS:FF` même en DF**, avec au moins deux chiffres par composant (FF peut en avoir trois); aucun point-virgule n'est ajouté implicitement.

`move_clip()` et `split_clip()` utilisent le même nominal et le même compte de labels DF que le moteur partagé; leurs garde-fous structurels restent inchangés. Les opérations exprimées en TC n'offrent pas de paramètre subframe; les méthodes exprimées en échantillons restent sample-accurate.

Cette extension de lecture/conversion **n'élargit pas** les profils de création/import stricts 48 kHz / 23.976 du builder, des groupes vides et de l'import natif. Une session 47.952 lue depuis son enum exact `0x0F` ne peut plus se faire passer pour `0x09` grâce à son premier octet. Preuves et limites de validation : annexe A (§1.4).

## 2. Pointeurs, relocalisation et sauvegarde

### 2.1 Bloc spécial `0x0001`

Le premier bloc commence obligatoirement à l'offset `0x14` :

```text
5A | block_type=0x0001 | block_size=4 | pointeur UInt32 vers 0x0002
```

Il ne possède pas de `content_type` physique. Le parseur générique place néanmoins les deux octets bas du pointeur dans `PTBlock.content_type` et les deux octets hauts dans `items[0]`. Cette pseudo-valeur peut collisionner avec n'importe quel vrai `content_type`; toutes les recherches de racines métier excluent donc explicitement ce premier bloc.

Le bloc doit mesurer exactement 11 octets, rester le premier bloc et pointer vers l'offset absolu de l'unique `0x0002` final. Après sauvegarde, sa représentation en mémoire est resynchronisée avec les quatre octets réellement écrits.

### 2.2 Bloc final `0x0002`

`0x0002` est une racine plate, unique, finale et terminée exactement à la fin du fichier. Ses segments `bytes`/`bytearray` sont concaténés sans perte.

Un enregistrement standard mesure 15 octets :

```text
00 00 00 01 04 00 01 00 | cible UInt32 | 00 00 00
```

- Le préfixe de huit octets et le suffixe nul de trois octets sont exacts.
- Les enregistrements consécutifs forment une série. Les deux octets immédiatement avant la série contiennent son nombre d'enregistrements en UInt16 big-endian.
- Le compteur doit être présent et exactement égal à la longueur de la série.
- Chaque cible standard doit être l'`original_offset` d'un bloc réellement parsé.
- Les octets entre les séries sont des métadonnées indépendantes et sont toujours conservés.
- Ces métadonnées peuvent contenir d'autres offsets absolus. Lors de la sauvegarde, le code examine chaque alignement possible de quatre octets hors des enregistrements standards et ne remplace que les valeurs présentes dans la table `ancien_offset → nouvel_offset`. À l'intérieur d'un enregistrement, seul le champ à `+8` est admissible.
- Deux patchs de quatre octets ne peuvent pas se chevaucher.

Lorsqu'un bloc est supprimé, tous ses offsets et ceux de ses descendants sont placés dans `_removed_offsets`. La purge générique retire uniquement les 15 octets de chaque enregistrement standard correspondant et décrémente le compteur UInt16 BE de sa série; elle ne supprime jamais les métadonnées voisines. `delete_tracks()` possède une exception strictement profilée documentée en §4.1.2 : trois groupes de métadonnées de piste, observés natifs, sont retirés comme unités complètes avec leurs records standards incorporés. Aucune autre métadonnée voisine n'est supprimée par inférence.

### 2.3 Pipeline de `save()`

La sauvegarde est transactionnelle en mémoire et atomique sur disque :

1. Sérialiser virtuellement toutes les racines pour produire la table globale des anciens et nouveaux offsets.
2. Revalider `0x0001`, l'unique `0x0002` final, son format plat, ses compteurs et les métadonnées temporelles.
3. Capturer l'identité des records et, pour une suppression de pistes vérifiée, les groupes de métadonnées natifs à retirer.
4. Relocaliser les pointeurs standards et secondaires de `0x0002`, mettre à jour les compteurs/mirroirs de suppression de pistes, puis purger les records et groupes capturés.
5. Sérialiser de nouveau, calculer l'offset final de `0x0002` et patcher `0x0001`.
6. Préserver l'en-tête déchiffré, rechiffrer une copie dans un fichier temporaire, rafraîchir tous les `original_offset`, puis remplacer atomiquement la destination.
7. Après succès, mettre à jour `self.data` avec les octets déchiffrés, vider `_removed_offsets` et les types associés, puis stocker le chemin absolu de sortie dans `file_path`. `save()` retourne `None`.

Toute exception antérieure au remplacement restaure `root_items`, `_removed_offsets` et `file_path`. Pour les sessions prises en charge, un chargement suivi d'une sauvegarde sans mutation est byte-for-byte identique.

## 3. Catalogue des blocs compris par l'API

Les `block_type` ci-dessous sont ceux observés dans les sessions de référence. À l'exception de `0x0001`, le code métier sélectionne généralement les blocs par `content_type` et valide leur disposition, pas leur `block_type` historique.

| `content_type` | `block_type` observé | Rôle pris en charge |
|---|---:|---|
| spécial `0x0001` | `0x01` | Pointeur initial vers `0x0002`; aucun `content_type` physique. |
| `0x0002` | `0x02` | Table finale des pointeurs. |
| `0x1004` | `0x03` | Racine des liens/noms de fichiers physiques. |
| `0x1028` | `0x0a` | Fréquence d'échantillonnage. |
| `0x103a` | `0x01` | Entrée de nom ou chemin sous `0x1004`. |
| `0x104f` | `0x0a` | Payload fixe d'un événement audio ou fondu. |
| `0x1050` | `0x03` | Conteneur d'événement de timeline. |
| `0x1052` | `0x03` | Playlist visible ou cachée. |
| `0x1054` | `0x02` | Map comptée de playlists. |
| `0x2030` | `0x05` | Conteneur générique; une disposition précise représente la règle des marqueurs. |
| `0x204d` | `0x05` | Enum de cadence d'image. |
| `0x2077` | `0x12` | Définition d'un marqueur. |
| `0x2423` | `0x04` | Entrée d'index de nom de Clip Group. |
| `0x2424` | `0x01` | Liste comptée des `0x2423`. |
| `0x2425` | `0x02` | Métadonnée parallèle d'un Clip Group. |
| `0x2426` | `0x01` | Liste comptée des `0x2425`. |
| `0x2428` | `0x01` | Timeline cachée des composants d'un Clip Group. |
| `0x2506` | `0x03` | Sous-bloc du modèle de marqueur créé par l'API. |
| `0x2523` | `0x09` | Métadonnée observée dans une définition de groupe; conservée, mais non utilisée par le dégroupage. |
| `0x2602` | `0x0e` | Définition d'un chemin I/O live; seul le chemin mono autonome sélectionné est interprété par le reader de sortie. |
| `0x2603` | `0x02` | Catalogue compté de chemins I/O live. |
| `0x260a` | `0x01` | Lane plate de Volume ou de Pan selon son propriétaire; profils distincts en §10. |
| `0x260d` | `0x05` | État/automation de piste, contenant aussi la sortie principale observée. |
| `0x260e` | `0x09` | Descripteur de sortie principale; profils bus mono/stéréo stricts (§4.1.3–4.1.8). |
| `0x2619` | `0x09` | Nom interne de piste. |
| `0x261b` | `0x0d` | Configuration de slot contenant identité et état de piste. |
| `0x261c` | `0x04` | Définition de piste et automation. |
| `0x2628` | `0x04` | Nom et attributs d'un clip ou groupe. |
| `0x2629` | `0x0b` | Définition de clip audio. |
| `0x262a` | `0x01` | Liste globale comptée des clips audio. |
| `0x262b` | `0x01` | Définition de Clip Group. |
| `0x262c` | `0x01` | Liste globale comptée des Clip Groups. |
| `0x262f` | `0x02` | Géométrie plate de fondu. |
| `0x2630` | `0x01` | Liste globale comptée des géométries. |
| `0x2637` | `0x01` | Dictionnaire plat de records de Clip Gain statiques; enveloppes trois/quatre nœuds préservables dans les extensions non publiées §9.2/§9.5. |
| `0x4826` | `0x01` | Sous-bloc du modèle de marqueur; deux occurrences. |
| `0x4827` | `0x01` | Sous-bloc du modèle de marqueur. |

## 4. Pistes et événements de timeline

### 4.1 Map `0x1054` et playlist `0x1052`

La timeline principale est l'unique racine `0x1054`. L'absence de cette racine produit une liste de pistes vide; plusieurs racines sont ambiguës.

- Le premier segment de `0x1054` commence par le nombre de `0x1052` directs en UInt32 LE.
- Le premier segment de chaque `0x1052` est `[name_len UInt32][name UTF-8][event_count UInt32]`.
- Le nom explicite doit être UTF-8 et non vide, sauf le profil anonyme mono en lecture seule détaillé ci-dessous. Le compteur doit toujours égaler le nombre de `0x1050` directement enfants, même pour une playlist sans nom.
- Les segments bruts et trailers non interprétés conservent leur position.

`get_tracks()` retourne les noms de toutes les playlists de la timeline principale, après validation complète de la map et de tous ses compteurs. Il ne filtre pas les slots masqués par l'interface Pro Tools. Sans racine `0x1054`, il retourne `[]`.

**Basic Folder, observation de lecture** : le profil observé possède deux miroirs `0x251a` sous `0x2519`, de premier octet `0x0b` (Audio : `0x00`), longueur du nom UInt32 à `+2` et nom UTF-8 à `+6`. Ce dossier n'est pas une playlist audio principale `0x1052`; son nom n'est donc pas ajouté aux résultats de `get_tracks()` par simple présence dans le catalogue. Les playlists nommées restent lues depuis la map principale, sans reconstruire leur appartenance au dossier. Nom du dossier, noms/nombre de pistes et placements de clips ne sont pas des constantes du profil. Les données de dossier non interprétées sont préservées; aucune création, modification ou reconstruction de dossier n'est implémentée. L'observation ne valide pas les Routing Folders, l'imbrication ni les folders anonymes. Validation : annexe A (§4.1).

**Playlists anonymes ou mixtes mono, lecture non publiée** : chaque header principal anonyme du profil vérifié est exactement `[0 UInt32][event_count UInt32]`, soit 8 octets. Le nom de la piste est résolu depuis les miroirs indépendants suivants, et non deviné depuis un clip ou un filename. `_validated_audio_playlist_names()` réutilise `_validated_audio_track_identities()`; le wrapper historique `_validated_anonymous_mono_track_names(N)` reste disponible aux autres resolvers mono. Tous les noms, identités, compteurs et ordinaux doivent concorder; les noms déjà écrits doivent égaler byte pour byte le nom UTF-8 attendu. Le nombre de playlists et la valeur de leurs noms sont variables; une simple égalité de nombres ne suffit jamais. Validation : annexe A (§4.1) et paire native de renommage mono, qui matérialise une seule playlist et laisse l'autre anonyme.

Chaque racine ci-dessous doit être unique. Les offsets de queue `T` sont relatifs à la fin du nom UTF-8 du record concerné, et non au bloc complet. Les noms sont non vides, sans NUL, UTF-8 strict; les noms de descripteur et identités UI sont uniques.

| Structure | Validation exacte du resolver |
|---|---|
| `0x1015 → 0x1014` | Premier segment brut de 4 octets, compteur UInt32 `N`; `N` descripteurs directs, chacun avec un seul segment brut. Nom : longueur UInt32 à `+0`, bytes à `+4`. Queue `T` de 39 octets : `T[0:5] = 00 01 00 00 00`; UInt32 à `T+5` et `T+30` égaux à l'ordinal `i` (zero-based); `T[11:15] = 2A 00 00 00`. Les autres champs sont opaques; l'identité propre au descripteur n'est pas assimilée à l'identité UI. |
| `0x2107 → 0x210b` | Préfixe brut de 13 octets, octet `+8 = 01`, compteur UInt32 à `+9 = N`; `N` entrées directes à un segment brut. Nom : longueur à `+4`, bytes à `+8`, identique au descripteur de même ordinal. Queue de 32 octets : magic à `T+4`, identité UI de 8 octets à `T+8`, unique et non nulle. |
| `0x2519 → 0x251a` | Exactement `2N` miroirs directs. Les items `1..N` et `N+2..2N+1` sont les deux familles; item `N+1` = compteur brut de 4 octets égal à `N`. Chaque premier segment commence par `00 00` (Audio), longueur du nom UInt32 à `+2`, bytes à `+6`, même nom ordonné que `0x1014`/`0x210b`. Queue de 42 octets : six zéros initiaux (profil mono), magic à `T+6`, identité UI à `T+10`, UInt32 à `T+18 = i+1`, magic à `T+28`, même identité à `T+32`. Les champs restants, agrégat initial et flags UI sont préservés, pas normalisés. |
| `0x2624 → 0x261c → 0x261b → 0x102d → 0x2619` | Préfixe brut de 4 octets, compteur `N`, `N` slots directs. Chaque étape du chemin contient exactement un enfant du type suivant, et chaque slot contient exactement un `0x2619` au total. Dernier segment de `0x2619` : brut de 18 octets, magic à `+4`, identité à `+8`, identique à l'identité UI du même ordinal. Le libellé historique/configuration du premier segment reste opaque. |

Les deux magics de miroir et celui du slot valent `2A 00 00 00`. Les compteurs `0x1054`/`0x1052` sont validés **avant** la résolution par `_validated_main_playlist_records()`. Les sessions entièrement nommées gardent leur chemin historique, sans exiger ce nouveau profil de catalogue. En présence d'un nom manquant, toutes les pistes doivent suivre le profil mono corroboré; un mélange nommé/anonyme cohérent est accepté, pas un nom existant contradictoire. Un header anonyme de longueur différente de 8, une piste stéréo ou non Audio, un folder, un ordinal d'affichage masqué/réordonné, un nom/identifiant/compteur contradictoire ou une structure inconnue provoque une erreur explicite; aucune playlist n'est silencieusement omise par ce resolver.

`get_tracks()`, `get_timeline_clips()` et `get_timeline_clip_groups()` demandent explicitement `_validated_main_playlists(allow_anonymous=True)` (ou le même paramètre du validateur d'événements). Les noms sont uniquement ceux des tuples de lecture : headers, octets opaques, événements, catalogues et pointeurs ne changent pas; aucun cache de correspondance. Le défaut `allow_anonymous=False` reste celui des validateurs de mutation : les writers dépendant de la map principale refusent toujours `Track name cannot be empty.`. Le remplissage explicite §4.1.9 est une opération distincte, pas un assouplissement de ces writers. L'API ne lit/construit pas l'AAF et ne convertit pas les médias.

Ce resolver reconstruit les noms en lecture seule; il n'implique pas une compatibilité générale avec toutes les sessions provenant d'un AAF. Il ne convertit ni la session ni les médias. Les exemples de longueurs, placements, noms et fichiers consolidés appartiennent uniquement à l'annexe A (§4.1).

### 4.1.1 Pool de pistes précréées : renommage et visibilité

L'API ne crée ni ne réordonne de piste. Une application peut employer un pool de pistes Audio déjà créées dans une template Pro Tools : `rename_track()` met à jour les miroirs de nom vérifiés, `set_visible_tracks(track_names)` affiche exactement les slots demandés, dans l'ordre fourni, et `delete_tracks()` peut retirer les slots vides du profil strict documenté en §4.1.2.

La visibilité native est dupliquée dans trois emplacements qui doivent rester cohérents :

- les deux familles de `0x251a` sous `0x2519` : l'octet après l'identité `2A 00 00 00` vaut l'ordinal one-based de l'affichage, ou `0` si la piste est masquée; le flag court de 11 octets passe respectivement à `... 01 01 00 00 FE FF` ou `... 00 01 00 00 FE FF`;
- l'entrée agrégée `0x2519`, qui contient le même ordinal pour chaque piste;
- l'état `0x2589` : UInt16 LE à `+2` vaut `1` (affichée) ou `0` (masquée), et le payload court final vaut respectivement `00 00 00 00 00` ou `01 00 01 00 00`.

Dans le record court de 11 octets de `0x251a`, les neuf premiers octets ont la forme vérifiée `00 00 00 00 | visible | 01 00 00 00`; l'octet `visible` à `+4` est le seul modifié par l'API. Le UInt16 final (`+9..+10`) est opaque et peut varier selon la piste dans une même template (par exemple `FE FF`, `1E 00`, `2D 00`). `set_visible_tracks()` le conserve byte-for-byte et ne l'utilise jamais comme constante d'identification.

`set_visible_tracks()` exige une liste non vide, sans doublon, composée uniquement de noms de pistes existants. Il ne modifie ni le nombre de playlists, ni les offsets, ni le catalogue média, et restaure transactionnellement l'arbre en cas de profil ambigu ou incomplet. Aucun nom ou ordinal particulier du corpus n'est imposé à l'appelant. Validation : annexe A (§4.1.1).

Un pool de pistes nouvellement créé doit être ouvert puis sauvegardé normalement une fois dans Pro Tools avant son premier `rename_track()`. Le profil UI pré-normalisation observé dans certaines templates de pool est lisible et peut servir au contrôle de visibilité, mais un renommage y ferait rejeter la map complète de pistes par Pro Tools. L'API reconnaît ce profil exact et retourne `ValueError` avant toute mutation; elle ne tente pas d'imiter une normalisation interne non documentée.

### 4.1.2 Suppression de pistes Audio vides (`delete_tracks`)

`delete_tracks(track_names)` est une mutation de template, pas un destructeur général de pistes Pro Tools. `track_names` doit être un itérable non vide de `str` uniques, sans NUL, qui désignent des playlists `0x1052` principales existantes. Au moins une piste Audio doit rester. Toutes les playlists principales doivent être vides : un compteur d'événements non nul ou un enfant événement provoque `NotImplementedError` avant toute écriture.

Le profil d'écriture exige une occurrence unique et cohérente de chacun des miroirs suivants. Les noms et compteurs doivent tous correspondre à l'ordre de `0x1054` :

- `0x1054 → 0x1052` : compteur et playlists principales; les playlists supprimées disparaissent.
- `0x1015 → 0x1014` : compteur, descripteurs de noms et leurs deux ordinaux UInt32; les ordinaux restants deviennent `0..N-1`.
- `0x2107 → 0x210b` : compteur et métadonnées de noms; les identités restantes sont conservées.
- `0x2519` : agrégat de noms, deux familles directes `0x251a` et leur compteur intermédiaire; les ordinaux d'affichage restants deviennent `1..N`.
- `0x2587 → 0x258a → 0x2589` : compteur et états de piste. Les états d'ID final hors du domaine compact sont retirés; les états restants sont sérialisés dans l'ordre canonique croissant `0..N-1`. Cet ordre ne dépend ni des noms, ni de leur position d'origine.
- `0x2624 → 0x261c` : compteur et slots. Les libellés opaques historiques (par exemple `Audio N`) sont conservés sans être des noms visibles requis, mais les deux records d'ordinal du slot (`12` et `24` octets) sont compactés vers `0..N-1`.
- `0x202b → 0x202a` : les deux listes d'indices UInt16 deviennent exactement `0..N-1` avec leur compteur UInt32.

La table finale `0x0002` possède aussi trois miroirs bruts de cette topologie. Le writer les reconnaît seulement dans leur forme native exacte : (1) la liste de pointeurs des premiers `0x251a`, dont la longueur octet et le compteur BE sont compactés; (2) les entrées de 45 octets des seconds `0x251a`; (3) les entrées de 67 octets des slots `0x261c`. Les deux dernières entrées incorporent chacune un record standard de 15 octets. Elles sont capturées avant relocalisation, leurs records ne sont comptés qu'une fois lors de la purge, et leurs ordinaux restants sont compactés. Les six compteurs `0x251b`, le compteur voisin `0x2716`, le compteur de tête BE et le trailer final `0x261c` observé sont mis à jour suivant le profil. Une ambiguïté de taille, de type, d'ordinal, de compteur, de pointeur ou de recouvrement interrompt la transaction.

Ce chemin a été validé dans Pro Tools sur des suppressions simple en première/milieu/dernière position, doubles adjacentes et non adjacentes, aux extrémités, et triple. Il ne prend pas en charge une piste peuplée (clips, fades, groupes, automation ou tout autre événement), ni MIDI, vidéo, routing, inserts, sends, playlists alternatives ou layouts de miroirs non observés.

### 4.1.3 Lecture de sortie bus (`get_track_outputs`, non publié)

Le contrat est `[(track_name, [output_name]), ...]`, dans l'ordre des playlists principales. Ce profil expose une seule sortie principale par piste, avec une liste pour ne pas imposer une future représentation incompatible. Le bus peut être mono ou stéréo autonome (§4.1.6), mais la piste Audio reste mono. En l'absence de playlist principale (ou avec zéro piste), retourne `[]` après validation de la map. Sinon, toutes les pistes doivent correspondre au profil d'identités Audio mono corroboré de §4.1 : `_validated_main_playlists(allow_anonymous=True)` puis `_validated_anonymous_mono_track_names(N)`, avec égalité des noms ordonnés. Cela accepte les playlists entièrement nommées et les formes mono anonymes ou mixtes cohérentes de §4.1; cela n'étend pas aux pistes stéréo, folders ou ordinaux UI masqués/réordonnés. Aucune liste partielle n'est retournée en cas de refus.

La résolution travaille exclusivement sur l'arbre courant : les GUID des slots et des deux familles de miroirs sont validés par le helper d'identité; aucune recherche de magic dans `self.data`, aucun ancien offset, aucun cache. Un résultat retourné n'alias pas les données de l'arbre. Le garde-fou de `rename_track()` reste indépendant : l'une des références natives I/O est lisible par ce reader mais conserve un profil UI explicitement refusé par le writer de renommage existant; ce reader ne l'assouplit pas.

**Descripteur principal** : chaque slot doit avoir exactement un `0x260d` direct sous son `0x261b` déjà validé, puis exactement un `0x260e` direct. Le slot entier doit également contenir un seul descendant `0x260e`; un second record, un autre emplacement ou une sortie absente est hors profil. Le `0x260e` doit contenir un seul segment brut :

| Offset dans le payload `0x260e` | Champ vérifié/traité |
|---|---|
| `+0` | Code UInt8 observé; conservé et non interprété/calibré par ce reader. |
| `+1..+7` | `00 01 01 00 00 00 00`. |
| `+8..+11` | `2A 00 00 00`. |
| `+12..+19` | Identité de sortie de 8 octets, non nulle et corroborée par le catalogue live. |
| `+20..+35` | 16 zéros. |
| `+36` | Longueur du nom de sortie UInt32 LE. |
| `+40` | Nom UTF-8 strict, non vide et sans NUL. |
| Après le nom | Trailer mono exact de 19 octets `00 00 FF FF FF FF FF FF FF FF 00 FF FF FF FF 00 0C 00 00`, ou trailer stéréo de §4.1.6. Doit correspondre à la largeur du bus sélectionné; autres trailers refusés, pas normalisés. |

**Catalogue sélectionné** : une unique racine `0x2603`, avec un premier segment brut de 4 octets dont le compteur UInt32 égale le nombre d'enfants directs `0x2602`. Des `0x2602` imbriqués sont refusés. Les bibliothèques `0x4501`/`0x4702` ne sont jamais utilisées comme fallback. Chaque premier segment `0x2602` doit avoir un nom de longueur UInt32 à `+2`, bytes à `+6`, UTF-8 non vide/sans NUL. Les queues des chemins non sélectionnés restent opaques, sauf l'indexation d'identité des records mono/stéréo de géométrie fixe reconnue (§4.1.6); aucune classification multicanal par port ou recherche de magic variable n'est faite.

Le nom du descripteur sélectionne exactement une entrée de catalogue. Pour une sortie mono, le préfixe doit être `02 00` et la queue `T`, relative à la fin du nom, doit être exactement longue de 42 octets. Pour une sortie stéréo, utiliser les validations distinctes de §4.1.6.

| Offset dans `T` du chemin mono sélectionné | Validation |
|---|---|
| `+0..+3` | `01 00 00 00` (profil à un canal). |
| `+4..+5` | Port A UInt16 natif, conservé sans déduire un ordinal de catalogue. |
| `+6..+7` | `FF FF` : chemin mono autonome, pas un sous-canal. |
| `+8..+13` | `01 00 00 00 00 00`, incluant le flag bus nul à `+10`. |
| `+14..+17` | `2A 00 00 00`. |
| `+18..+25` | Même identité que le descripteur de sortie. |
| `+26..+41` | 16 zéros. |

L'identité sélectionnée doit être unique parmi les records mono reconnus par `02 00`, queue de 42/magic à `T+14`, et les records stéréo 44/magic à `T+16` de §4.1.6. Noms dupliqués, alias d'identité, incohérence entre nom/ID/largeur, chemin matériel, sous-canal ou largeur sélectionnée autre que les deux profils sont refusés. Le catalogue peut contenir d'autres largeurs non sélectionnées; elles ne sont ni publiées dans une liste générale de sorties, ni transformées. `get_available_outputs()` et le writer général du contributeur ne sont pas intégrés. La branche de réutilisation de §4.1.4 n'infère aucun code; la nouvelle calibration stricte de §4.1.5 reste séparée du reader, qui n'interprète pas les codes.

**Portée validée** : lecture des descripteurs principaux du profil ci-dessus et préservation des autres données. Codes, identités, noms, nombre de chemins et longueurs de records observés dans une session ne deviennent jamais des constantes du lecteur. Exemples et résultats : annexe A (§4.1.3).

Les modifications que Pro Tools apporte à ses états UI, identités opaques ou références de dossier lors d'une sauvegarde ne définissent pas une règle de routing générale. Le lecteur n'écrit rien; les profils d'écriture et de largeur de bus sont spécifiés séparément ci-dessous.

### 4.1.4 Réaffectation à un bus mono déjà utilisé (`set_track_output`, non publié)

**Statut : branches vers un bus déjà affecté et vers un bus calibré (§4.1.5) validées dans Pro Tools.** Ce writer n'est pas le writer général du contributeur. Signature : `set_track_output(track_name, output_name)`. Retourne `1` si l'unique descripteur principal cible est modifié, `0` si l'affectation est déjà identique. Aucune sauvegarde implicite; appeler `save()` séparément. Il ne crée pas de bus, de record de catalogue, de bloc ou de pointeur supplémentaire.

Les deux paramètres doivent être des chaînes non vides, sans NUL et encodables en UTF-8. Le writer exige d'abord `_validated_main_playlists()` sans autoriser les noms anonymes : le profil anonyme validé en lecture n'est pas implicitement autorisé en écriture. Puis `_validated_bus_track_outputs()` (partagé avec le reader de §4.1.3) prévalide toutes les pistes, identités et sorties de l'arbre courant, ainsi que le catalogue live. Toutes les restrictions structurelles de §4.1.3 s'appliquent. Chaque `0x260e` doit également être de `block_type 0x09`. Les extensions de largeur et pan sont décrites séparément en §4.1.6.

Il construit les correspondances depuis les **affectations actuelles**, pas depuis les seuls chemins disponibles du catalogue :

- pour un même nom de bus, tous les payloads de sortie employés doivent être byte-identiques; un code contradictoire est refusé;
- un même code UInt8 ne peut pas désigner deux bus différents parmi ces affectations;
- la piste cible doit exister dans le profil nommé vérifié;
- dans la branche de réutilisation, le bus cible est déjà affecté à au moins une piste actuelle de cette session. Sinon, les garde-fous supplémentaires de §4.1.5 s'appliquent; sa présence dans `0x2603` seule ne suffit jamais.

La branche de réutilisation n'effectue aucune calibration. Ni modulo `&0xFF`, recherche dans les bytes source, bibliothèque factory ni cache de codes n'est utilisé. Le port du catalogue ne devient pas un code de descripteur. Si la dernière affectation d'un bus disparaît, son descripteur ne reste pas en cache : un appel suivant doit satisfaire §4.1.5 pour y revenir. Les appels successifs lisent l'état actuel, sans conserver artificiellement des codes historiques.

**Écriture atomique par prévalidation et remplacement unique** : avant toute mutation, le writer prépare un payload neuf. Il conserve le préfixe `+0..+35` de la cible, remplace uniquement le code `+0` et l'identité de bus `+12..+19` par ceux du descripteur donneur vérifié, reprend la longueur/nom `+36` jusqu'à la fin du nom depuis ce donneur, puis conserve le trailer exact de 19 octets de la cible. Aucun état de piste n'est cloné. Toutes les géométries constantes et les zéros ont déjà été corroborés; le payload obtenu correspond au descripteur donneur sans alias mutable. Si identique au payload courant, retourne `0` sans remplacement. Sinon, seule l'affectation `target.items[0] = replacement` précède le retour `1`; aucune opération susceptible de validation tardive n'est effectuée ensuite.

Le bloc cible, son type, son `original_offset`, les autres descripteurs, catalogues et champs opaques de piste sont conservés. `_removed_offsets` n'est pas modifié. La taille peut augmenter ou diminuer avec le nom; le mécanisme existant de `save()` relocalise les blocs et pointeurs, plutôt qu'un patch à l'ancien offset dans `self.data`.

**Préservation** : nom et identité du bus donneur sont résolus depuis les affectations actuelles. Leur longueur peut différer de celle de la cible; aucune longueur, identité, valeur de code ou chaîne de bus issue du corpus n'est imposée. Les suffixes de slots, états UI et identités opaques modifiés par une sauvegarde Pro Tools ne sont pas copiés ou normalisés. Validation : annexe A (§4.1.4).

La branche de réutilisation est validée dans Pro Tools; elle ne suffit pas à justifier un code pour un bus inutilisé. La calibration exige les conditions de §4.1.5. Les maps anonymes/cachées/réordonnées et le routing matériel/multi-sorties/Sends restent hors profil.

### 4.1.5 Bus mono non affecté : calibration corroborée (non publié)

**Statut : extension locale de `set_track_output()`, sortie vers le bus inutilisé et retour au bus long validés dans Pro Tools.** Cette branche s'applique uniquement si aucun descripteur principal actuel n'emploie déjà le nom cible. Les validations communes de §4.1.3/§4.1.4 ont d'abord validé toutes les affectations existantes et la map principale nommée.

1. Exiger la racine live `0x2603` de `block_type 0x02` et tous ses `0x2602` directs de `block_type 0x0e`. Un type inconnu est refusé dans cette branche. Le compteur brut et l'absence de paths imbriqués ont déjà été contrôlés par le reader.
2. Énumérer **tous** les `0x2602` directs, sans filtrer les largeurs, sous-canaux ou sorties non sélectionnés : leur ordinal zéro-based est la position réelle dans ce catalogue, pas l'index d'une liste mono filtrée. Les noms UTF-8 et segments bruts ont déjà été contrôlés. Aucune bibliothèque `0x4501`/`0x4702` ni arbre source périmé n'est utilisé.
3. Le nom cible doit sélectionner une seule entrée. `_validated_bus_path_identity(payload, tail)`, partagé avec le reader, délègue au helper mono pour le profil de §4.1.3, ou valide le profil stéréo de §4.1.6. L'identité doit être unique parmi les entrées reconnues des deux géométries, y compris les alias mono/stéréo. Les ports sont conservés, pas utilisés comme code.
4. Exiger au moins **deux noms de bus distincts** actuellement assignés. Plusieurs pistes sur un seul bus ne suffisent pas. Pour chaque bus utilisé, calculer `base_candidate = descriptor_code - live_catalog_ordinal`. **Tous** ces candidats doivent être identiques et non négatifs; pas seulement les deux premiers.
5. Calculer `code_target = base + target_ordinal`; exiger `0 <= code_target <= 255`. Pas de masque, modulo, valeur constante ni ajustement automatique d'une base contradictoire.
6. Préparer un descripteur donneur neuf depuis le préfixe du descripteur actuel, le trailer vérifié de la largeur du bus cible, le code calibré, l'identité et le nom UTF-8 du bus validé. Pour Mono→Mono, utiliser le remplacement unique de §4.1.4; toutes les erreurs précèdent la mutation. Aucun catalogue, état de piste, cache ou pointeur supplémentaire n'est écrit dans cette branche. Mono→Stereo suit les préconditions de pan de §4.1.6.

**Base de calibration variable** : la base est calculée depuis les bus déjà affectés de la session courante, jamais depuis leurs noms ou une valeur mémorisée. Le code cible dépend de l'ordinal de l'entrée dans le catalogue live complet. Exemple numérique corroboré : annexe A (§4.1.5).

Les ports, IDs opaques, timestamps et références de dossier restent préservés selon les règles précédentes; ils ne sont pas utilisés pour ajuster une calibration incohérente.

Cette branche est validée pour le profil décrit. Elle ne constitue pas un moteur I/O général : les paths non reconnus, Sends, matériel, catalogues factory et maps anonymes ne deviennent pas écrits par cette calibration.

### 4.1.6 Piste Audio mono vers bus stéréo autonome (non publié)

**Statut : comparaison native, tests API et ouverture/sauvegarde/réouverture manuelle validés.** Il s'agit de la largeur du bus, pas d'une piste Audio stéréo. Les identités de piste restent soumises au profil mono corroboré de §4.1.3. `_validated_bus_track_outputs()` et `_validated_bus_path_identity()` ne classifient ni tous les layouts multicanaux, ni les sous-chemins, ni les sorties matérielles.

Le record sélectionné `0x2602` commence par `02 01`; nom UInt32 à `+2`, UTF-8 à `+6`. Sa queue `T` après le nom est exactement de 44 octets :

| Offset dans `T` stéréo | Validation |
|---|---|
| `+0..+3` | `02 00 00 00` (profil à deux canaux). |
| `+4..+7` | Deux ports UInt16 natifs; valeurs conservées, pas utilisées comme ordinals/codes. |
| `+8..+15` | `FF FF 01 00 00 00 00 00`, chemin autonome bus. |
| `+16..+19` | `2A 00 00 00`; ne pas employer l'offset mono `+14`. |
| `+20..+27` | Identité non nulle de 8 octets; ne pas employer l'offset mono `+18`. |
| `+28..+43` | 16 zéros. |

Le trailer stéréo `0x260e` après son nom est exactement `01 01 FF FF FF FF FF FF FF FF 00 FF FF FF FF 00 0C 01 00`, soit 19 octets. Les octets `0`, `1` et `17` du trailer passent de 0 à 1 par rapport au profil mono. Les autres champs du préfixe du descripteur restent ceux de §4.1.3. Nom, identité et largeur doivent tous concorder avec l'unique chemin sélectionné. L'index d'alias reconnaît les deux géométries fixes (mono 42/magic14, stéréo 44/magic16), même si un chemin non sélectionné est autrement invalide; une identité réutilisée entre ces records entraîne un refus. Le reader n'inspecte pas ni ne modifie le pan : il expose les sorties, pas les paramètres de pan.

**Writer Mono→Stereo :** la branche de code déjà affecté ou calibré de §4.1.4/§4.1.5 est conservée. La largeur du trailer vient du donneur/bus cible, jamais du descripteur mono actuel. Avant mutation, le slot ciblé doit contenir exactement deux descendants `0x260c`, tous deux directs dans son conteneur principal `0x260d`, de type `0x02`, chacun avec un seul segment brut de 14 zéros. Aucun état opaque ou pan préexistant non vide n'est effacé. Le premier conteneur conserve son bloc et offset, mais reçoit les items suivants; le second reste byte-identique :

```text
01
[0x260a, block_type=0x01, original_offset=0,
 payload=01 46 01 00 14 00 00 00 00 00 01 00 00 00
         02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00]
00 × 13
```

Cette lane de 30 octets contient un point statique au timestamp 0, valeur Int16 0 à `+26` (centre). Elle est reconstruite depuis ce profil natif exact, pas copiée depuis le pan d'une autre piste. Tous les refus et allocations précèdent le remplacement des items du pan et du payload de sortie. Aucun catalogue I/O, média, clip, autre piste ou offset existant n'est muté; `save()` relocalise les pointeurs existants. La comparaison native n'ajoute aucun pointeur standard `0x0002` pour la nouvelle lane et conserve les 172 records avec les mêmes métadonnées et cibles logiques.

Une affectation strictement identique retourne `0` sans modification du pan. Le retour vers un bus mono suit §4.1.7; le changement vers un autre bus stéréo suit §4.1.8, sans retrait du pan. Ces transitions sont validées pour leurs profils stricts. Ce writer de routing ne permet pas de régler un pan arbitraire ni de créer une piste Audio stéréo : Mono→Stereo exige deux conteneurs initialement vides, Stereo→Mono et Stereo→Stereo le seul pan statique centré vérifié.

**Portée de validation** : l'activation du pan centré et le descripteur stéréo sont corroborés conjointement, sans copier l'état d'une autre piste. Les changements natifs de dossiers/UI/identités/timestamps de sauvegarde ne sont pas transposés à l'API. Les valeurs de code, ports, noms et ordinaux de la paire sont des exemples uniquement (annexe A, §4.1.6).

La sortie de ce profil a été ouverte, sauvegardée et rouverte dans Pro Tools. Aucun retrait de pan ni autre transition n'est inféré de cette seule activation; appliquer les validations distinctes ci-dessous.

### 4.1.7 Retour bus stéréo vers bus mono, pan centré statique (non publié)

**Statut : comparaison native, tests API et ouverture/sauvegarde/réouverture Pro Tools validés.** La piste Audio reste mono. Les validations communes de §4.1.3 à §4.1.6, playlists nommées et code cible réutilisé/calibré restent obligatoires. La transition vers un autre bus stéréo est traitée séparément en §4.1.8, sans retrait du pan.

**Transformation fonctionnelle** : remplacer le descripteur par celui du bus mono vérifié et retirer uniquement la lane centrée selon les préconditions ci-dessous. Les autres pans, routes et données restent préservés. Exemple natif : annexe A (§4.1.7).

Le writer résout les deux conteneurs exactement au chemin principal déjà validé. Il exige deux `0x260c` directs/type `0x02`, et exactement deux descendants de ce type dans tout le slot. Le premier doit avoir **exactement trois items** : segment brut `01`, enfant `0x260a`/type `0x01` avec un seul payload brut égal aux 30 octets de `_BUS_CENTER_PAN` (§4.1.6), puis segment brut de 13 zéros. Le second doit être un seul segment de 14 zéros. Une autre valeur de pan, un autre timestamp, des flags différents, de l'automation, un enfant/segment supplémentaire ou un état opaque sont refusés; aucune donnée de pan arbitraire n'est effacée.

Si la lane possède un `original_offset` existant non nul, il faut une unique racine `0x0002`. `_raw_0002_payload()` et `_validate_0002_record_layout()` valident son payload actuel; ce profil exige également des records standards non vides (un payload sans records ne prouve pas l'absence de références). Puis leurs pointeurs UInt32 LE sont examinés : **toute référence à cet offset est refusée**, pas purgée expérimentalement. La validation porte sur les références réelles de la session courante, pas sur un nombre de records issu du corpus. Une lane créée en mémoire à offset 0 n'a pas de référence source à examiner.

Avant toute mutation, préparer le nouveau descripteur mono, les items vides du pan et des copies des métadonnées de suppression. `_collect_offsets_recursive()` et `_collect_offset_types_recursive()` enregistrent dans ces copies l'offset positif/type `0x260a` de la lane retirée. Puis seulement remplacer le payload de route, les items du premier pan, `_removed_offsets` et `_removed_block_types`. Le bloc parent, son offset et le deuxième pan restent conservés; les autres métadonnées de suppression préexistantes restent intactes. Aucun bus, record de catalogue ou média n'est créé. `save()` utilise ses mécanismes habituels de relocation/purge et réinitialise ces métadonnées après succès.

Une affectation identique retourne toujours `0` sans inspecter/modifier le pan. Après réduction réussie, un appel Mono→Stereo peut réinstaller la lane centrée depuis les conteneurs redevenus vides. Sur le before natif, ce retour composé reproduit tout le PTX **byte-identique**, y compris après enregistrement de l'offset retiré et passage par `save()`.

Ce profil de réduction a été validé dans Pro Tools. Les changements opaques d'UI, chemins, timestamps de sauvegarde et identités de clips restent hors de la mutation. Exemples, contrôles de refus et cycles de sauvegarde : annexe A (§4.1.7).

### 4.1.8 Bus stéréo vers autre bus stéréo, pan centré préservé (non publié)

**Statut : profil strict validé dans Pro Tools, non publié.** La piste Audio reste mono; il s'agit uniquement d'un changement entre deux bus autonomes stéréo de géométrie §4.1.6. Les exemples et incidents de préparation sont séparés de cette règle en annexe A (§4.1.8).

Le nom, l'identité et le code du bus peuvent changer, ainsi que la longueur du payload qui dépend de la longueur UTF-8 du nom. Le trailer stéréo reste celui de §4.1.6. Ordinal de catalogue, ports, code et identité sont des données propres à la session, pas des constantes de cette transition.

`set_track_output()` conserve les préconditions communes de map nommée, identité Audio mono, sortie principale unique, bus/nom/identité/largeur vérifiés et code réutilisé ou calibré. Après construction du descripteur neuf et rejet des contradictions, une affectation byte-identique retourne `0` avant inspection du pan. Pour une véritable transition Stereo→Stereo, résoudre le slot/`0x261b`/`0x260d` principal et exiger exactement deux `0x260c` directs/type `0x02`, sans autre descendant de ce type dans le slot. Le premier doit avoir exactement `[01][0x260a type01, payload brut unique égal aux 30 octets de _BUS_CENTER_PAN][00×13]`, le second un seul segment de 14 zéros, comme §4.1.7. Pan non centré, automation, timestamp/flags différents, segments/enfants supplémentaires et états inconnus restent refusés sans mutation.

Cette validation du pan est partagée avec la réduction Stereo→Mono, **pas ses mutations ni la vérification préalable des références de lane retirée** : aucune lane n'est retirée ici. Préparer puis remplacer **uniquement `target.items[0]`**, le payload de sortie. Conserver les deux conteneurs et leurs items/objets, la lane centrée, tous leurs `original_offset`, ainsi que les listes/dictionnaires préexistants `_removed_offsets`/`_removed_block_types`. Ne jamais cloner le pan de la piste donneuse, même si son code est réutilisé. Aucun nouveau pointeur, catalogue, bus, média ou clip n'est généré. `save()` relocalise les pointeurs existants selon son pipeline habituel.

Le profil est validé dans Pro Tools avec conservation du pan centré et des pointeurs. Le routing matériel/multicanal/Sends, la création de bus, les pistes Audio stéréo et le pan arbitraire restent hors portée. Détails de validation : annexe A (§4.1.8).

### 4.1.9 Matérialisation des noms manquants (`fill_anonymous_playlist_names`, non publié)

Signature sans argument, retour entier : nombre d'en-têtes `0x1052` remplis,
pas nombre de pistes physiques. Aucun save implicite ni renommage de piste.
Little-endian vérifié requis; map principale vide/absente retourne0. Toute map
non vide doit satisfaire `_validated_main_playlist_records()` : header/count
`0x1054`, noms UTF-8/bornes et compteur d'événements de chaque `0x1052`.

`_validated_audio_playlist_names(records, allow_stereo=True)` exige une racine
`0x1015` unique. Le nombre de descripteurs directs donne le compte des pistes,
ensuite **tous** les compteurs/métadonnées/miroirs/identités/ordinaux et largeurs
doivent satisfaire `_validated_audio_track_identities()` (§4.1 et10.3).
Un simple compte ou un slot GUID trouvé par scan ne suffit pas. Développer
chaque nom selon sa largeur vérifiée : mono un header, stéréo deux headers
consécutifs du même nom. La somme doit égaler le nombre de playlists principales;
les index de canaux répétés dans les descripteurs et les ordinaux des slots
corroborent cette association. Zéro canal n'est jamais remplacé par un.

Pour **chaque** record, y compris un nommé/no-op : le nom existant brut doit
égaler les octets UTF-8 du nom corroboré; aucune correction silencieuse,
latin1, strip-NUL ou nom partiel. Si nlen=0, header exactement8 octets requis.
Une queue anonyme inconnue n'est ni supprimée ni reconstruite : erreur avant
mutation. Les queues de headers déjà nommés sont laissées intactes.

Préparer tous les payloads avant affectation. Pour chaque header anonyme :
`UInt32LE(len(name_bytes)) + name_bytes + old_header[4:]`. La longueur tient
sur UInt32; le compteur d'événements est ainsi retenu byte-identique, pas
recalculé. Ne changer ni événement, UUID, catalogue, metadata, bin, média,
nom déjà explicite, compteur de map, objet bloc, offset original ou bookkeeping
de suppression. `save()` utilise ensuite la relocalisation TOC existante.
Retour0 sur map entièrement nommée **après validation**, sans remplacer d'objet.

La paire native mono démontre qu'un rename matérialise un header et laisse
l'autre anonyme; elle ne prouve pas que Save As matérialise tous les noms.
Le candidat remplit uniquement le second header, sans modifier celui créé
nativement;172 cibles TOC et géométrie audio exactes, deux cycles byte-identiques.
Seize régressions de nommage couvrent lecture mixte, UTF-8, schémas/counters/identités,
refus tardifs atomiques, no-op et composition des schémas stéréo déjà établis.
**Ouverture/lecture/save/réouverture Pro Tools du candidat mono validées**;
la sauvegarde native conserve noms, géométrie audio, médias et172 cibles TOC,
avec deux cycles API byte-identiques et remplissage no-op. Quatre régressions
supplémentaires corroborent la composition avec le resolver Volume existant :
identités live, pas de repli cache/ordinal, ciblage unique et restauration exacte.
Suite638 réussie,60 fichiers Python3.8/Pyflakes propres. Le ciblage de cette
composition Volume est également validé dans Pro Tools : lane témoin et
géométrie audio conservées, save natif et cycles exacts. Un déplacement manuel
de point confirmé par l'utilisateur est conservé et distingué de l'écriture
API, sans compensation (annexe A.39). Pas d'oracle natif
AAF stéréo revendiqué; aucune extension implicite des autres writers anonymes
ou des profils folders/cachés/réordonnés/non Audio.

### 4.2 Événement `0x1050 → 0x104f`

Chaque événement pris en charge contient exactement un `0x104f` brut d'au moins 16 octets. `duplicate_clip()` et les deux trims exigent le format vérifié de 35 octets; les événements Fade créés par l'API mesurent aussi 35 octets.

| Offset `0x104f` | Taille | Signification |
|---|---:|---|
| `+0` | 1 | Mute statique : `0x00` actif, `0x01` muté. |
| `+2` | 4 | ID ordinal. Pour l'audio, index dans `0x262a`; pour un fondu, index dans `0x2630`. |
| `+7` | 8 | Timestamp absolu en échantillons, UInt64 LE. |
| `+15` | 1 | Type : `0x03` audio/macro, `0x01` fondu. |
| `+33` | 1 | Liaison native utilisée par le fondu et le fragment droit d'un crossfade. |

Le payload secondaire brut direct de `0x1050` distingue les namespaces :

| Type | `block_type` du `0x104f` créé | Queue secondaire |
|---|---:|---|
| Placement audio | observé/recopié; `0x0a` dans le builder audio | `00 01 01`, ou la variante observée `01 01 01` lorsque `0x104f[15] == 0x03` |
| Macro Clip Group | observé/recopié | `00 00 01` |
| Fondu | `0x0a` | `01 01 01` |

Une macro de groupe n'est jamais un clip audio, même si son ID numérique est égal à un ID de `0x262a`. Les événements d'autres types sont préservés mais ne sont pas retournés par `get_timeline_clips()`.

`get_timeline_clip_groups()` est le lecteur public dédié aux macros visibles de la timeline principale. Il valide une unique racine `0x262c` (compteur UInt32 et `0x262b` directs), attribue l'ID de groupe par ordinal zéro-based dans cette liste, puis parcourt les playlists validées de la racine principale `0x1054`. Pour chaque événement `0x104f[15] == 0x03` dont la queue secondaire est exactement `00 00 01`, il lit l'ID UInt32 LE à `+2` et le timestamp UInt64 LE à `+7`; la durée provient du payload `0x2628` de la définition `0x262b` correspondante. Le résultat est trié par `(start_samples, track)` et contient chaque occurrence, y compris les placements répétés d'un même groupe : `group_id`, `group_name`, `track`, `start_samples`, `length_samples`, `end_samples`, `start_timecode`, `length_timecode`, `end_timecode`. Un ID de macro absent de `0x262c` est une incohérence rejetée. Cette méthode est strictement en lecture seule et ne modifie pas le contrat audio de `get_timeline_clips()`.

`get_timeline_clips(include_fades=True)` retourne les événements audio et fondus triés par `(start_samples, track)`. La durée audio et le `src_offset` viennent de `0x2628`; la durée et le début d'un fondu viennent de sa géométrie. Les macros de groupes sont exclues. Sans racine `0x262a`, il retourne immédiatement `[]`.

Avec `include_fades=False`, le lecteur retourne uniquement les placements audio et ne valide pas `0x2630`, les géométries `0x262f` ni leurs liaisons aux événements Fade. Ce mode sert aux consommateurs qui n'ont besoin que de l'audio dans une session contenant des géométries de fondu encore inconnues; il ne rend pas ces géométries éditables. Le paramètre doit être un booléen.

## 5. Dictionnaires de clips et résolution des fichiers physiques

### 5.1 Liste audio `0x262a`

Lorsqu'une racine `0x262a` existe, son premier segment brut commence par le nombre de `0x2629` directs en UInt32 LE. `get_clips()` autorise son absence mais refuse plusieurs racines; les opérations ciblant un clip en exigent exactement une. L'ID d'un clip est strictement son ordinal zéro-based dans cette liste. Chaque `0x2629` pris en charge contient exactement un `0x2628` brut.

`get_clips()` valide séparément `0x262a` et `0x262c`, puis retourne les clips audio (`parent`/`virtual`) et les groupes (`group`) avec longueur et décalage convertis en timecode. L'absence des deux listes produit `[]`.

Le payload `0x2628` commence par :

```text
name_len UInt32 LE | name UTF-8 | flags UInt16 LE | queue dépendante du flag
```

Il n'existe aucun padding d'alignement après le nom. Ajouter un octet pour aligner une chaîne impaire décale immédiatement la queue et les blocs suivants.

Soit `A = 4 + name_len`, l'offset des flags :

| Flags | Type retourné | Offset source | Offset de la longueur |
|---|---|---|---|
| `0x0000`, `0x0001` | bit faible `0` : `parent`; bit faible `1` : `virtual` | absent, donc `src_offset = 0` | `A+5` |
| `0x1001` (non publié) | `virtual` | UInt8 à `A+5` | `A+6` |
| `0x2000`, `0x2001` | même règle du bit faible | UInt16 à `A+5` | `A+7` |
| `0x3000`, `0x3001` | même règle du bit faible | UInt24 à `A+5` | `A+8` |
| `0x4001` | `virtual` | UInt32 à `A+5` | `A+9` |

Le sélecteur indépendant à `A+2` donne la largeur de la longueur : `0x10` = UInt8, `0x20` = UInt16, `0x30` = UInt24 et `0x40` = UInt32. Cette règle s'applique à toutes les familles ci-dessus. Des sessions de production ont confirmé les variantes parent `0x2000`/`0x3000`, les sélecteurs compacts `0x10`/`0x20`, ainsi que le passage de `0x3001` à `0x4001` sur une source longue.

Pour l'écriture `relink_clip()`, le marqueur suivant le sélecteur est normalement `(high_nibble(flag) | 0x04)`, suivi de `0x08`. Une variante native Pro Tools/RX validée ajoute précisément `flag=0x3000`, sélecteur `0x20`, marqueur `0x44`, constante `0x08` : elle contient donc un offset source UInt24, une longueur UInt16 et la référence incorporée UInt32 à `A+5+3+2`. Sur le corpus validé, cette référence vaut la référence `0x2106` du média physique plus l'offset source. Le writer conserve cette géométrie et recalcule la référence incorporée avec la même équation pour le nouveau média.

Les trois premiers octets doivent être distingués sans ambiguïté : `01 30 40` signifie flag UInt16 `0x3001`, puis sélecteur de longueur UInt32 `0x40`; `01 40 30` signifie le véritable flag UInt16 `0x4001`, puis sélecteur de longueur UInt24 `0x30`. Tout autre flag audio — notamment `0x1000` et `0x4000`, non vérifiés dans le corpus natif local — ou sélecteur est rejeté. La prise en charge d'une longueur UInt8 est indépendante de celle de l'offset source UInt8, ajouté uniquement pour `0x1001`. La valeur maximale d'un champ UInt8 est `255`, celle d'un UInt16 `65 535`, celle d'un UInt24 `16 777 215` et celle d'un UInt32 `4 294 967 295`.

**Offset compact `0x1001`, non publié** : la largeur UInt8 source déplace la longueur et les champs suivants comme indiqué dans la matrice; leurs valeurs dépendent de la définition et du placement courants, pas d'un trim de taille fixe. Le marqueur observé `0x44` et les autres champs opaques sont préservés, pas normalisés vers un marqueur supposé `0x14`. `get_relink_write_status()` retourne `supported=False`, code `unsupported_clip_layout`; `relink_clip()` refuse ce profil sans mutation ni clone WAV. Le candidat de trim en place §6.7 modifie seulement le profil UInt24-longueur strictement corroboré, sans étendre les writers historiques de sous-clips ou le relink. Exemple natif sample-accurate : annexe A (§5.1); nouveau trim : A.36.

Le lecteur accepte toute combinaison observée de la matrice ci-dessus. Le writer demeure volontairement plus restreint : il ne produit que les layouts explicitement décrits dans les sections Split et Trimming/sous-clips.

Le parent issu de l'import mono-float utilise l'un des deux profils stricts du builder. Le profil historique `00 00 30 04 00` à partir de `A` porte une durée UInt24 à `A+5` et une référence temporelle UInt32 à `A+8`. Le profil `00 00 40 44 00` porte une durée UInt32 à `A+5` et deux références temporelles UInt32 à `A+9` et `A+13`. `build_audio_session()` sélectionne le profil depuis le template, conserve ses champs opaques et produit un parent à `src_offset=0`, avec un nom explicite ou, par défaut, le stem physique suivi de `.A1`. La durée et la référence BWF doivent tenir respectivement dans la largeur du profil et dans UInt32.

Une définition clonable contient aussi un segment d'identité brut de 48 octets dans `0x2629` :

- Clip ID UInt32 à `+0`.
- UUID RFC 4122 aux octets `+22..+37`.
- `create_subclip()` exige exactement une occurrence de ce segment. `split_clip()` parcourt les segments mutables de 48 octets et remplace l'UUID de chacun; les fixtures vérifiées en contiennent exactement un, mais le code du split ne fait pas lui-même ce contrôle d'unicité.

Dans les profils historiques vérifiés, l'index de Clip Gain signé Int32 se trouve dans les quatre octets commençant à `len(payload_2628) - 6`. L'index alternatif annoncé à -22 avec queue UUID moderne n'est pas validé; aucun fallback de distance par simple validité numérique n'est implémenté. Cet audit n'ajoute pas encore de détecteur structurel sûr de cette queue : ne pas utiliser le writer sur ce profil non vérifié, même si les octets lus à -6 semblent un index plausible.

### 5.2 Définition de groupe `0x262c → 0x262b → 0x2628`

Le flag vérifié d'un Clip Group est `0x5000`. Sa longueur totale est une valeur 24-bit à `A+10`; les champs observés à `A+13` et `A+17` sont temporels, mais le lecteur public ne les consomme pas. Un groupe est retourné avec `type="group"` et `src_offset=0`.

### 5.3 `physical_filename`

Le layout standard possède un lien physique ordinal exact : le segment brut direct de 104 octets d'un `0x2629` stocke à `+96` un index UInt32 little-endian dans les enfants directs `0x1003` de l'unique catalogue racine `0x1004`. Le premier segment de `0x1004` contient leur compteur UInt32; le premier segment de chaque `0x1003` contient son ordinal one-based. Un corpus de production contient des index bien supérieurs à 255 et confirme que les quatre octets `+96..+99` appartiennent au champ; le writer n'impose donc aucune limite artificielle à 255.

Le riche enfant direct `0x103a` correspondant commence par deux compteurs, le marqueur `0x01`, le nom UTF-8 `Audio Files`, quatre octets opaques, puis un enregistrement ordonné par `0x1003` :

```text
02 00 00 00 00 | name_len UInt32 | filename UTF-8 | suffixe de 4 octets
```

Deux suffixes ont été observés pour les WAV : `45 56 41 57` (`EVAW`) et `00 00 00 00`. Tous les enregistrements d'un même catalogue doivent employer la même variante. Le writer conserve exactement la variante source lorsqu'il insère un nouveau nom et refuse les suffixes inconnus ou mélangés.

La liste est suivie d'une queue hiérarchique. Elle ne représente pas un chemin de système de fichiers et ses libellés ne doivent jamais servir à construire le chemin du WAV :

```text
00 FF FF FF FF
pour chaque nœud i, i = 1..K :
    label_len UInt32 | label UTF-8 | opaque 4 octets | 01 | node_count_i UInt32
puis :
    terminal_label_len UInt32 | terminal_label UTF-8 | 00 00 00 00
```

`K` doit être au moins 1. Pour `N` fichiers, `node_count_i = N+i`; les deux compteurs initiaux valent respectivement `N+K+2` et `N+K+1`. Le writer valide toute la queue jusqu'au libellé terminal et incrémente les deux compteurs initiaux ainsi que chacun des `K` compteurs de nœud quand il ajoute un fichier.

Tous les compteurs hiérarchiques doivent être cohérents avant ajout d'un média, y compris les compteurs des nœuds intermédiaires. Un catalogue partiellement actualisé peut sembler lisible dans l'importeur sans être ouvrable comme session. Les cas `K=1` et `K=3` et leur validation sont consignés en annexe A (§5.3).

Les libellés et compteurs de la queue sont des métadonnées PTX opaques, pas des dossiers du poste courant. Le dossier physique conventionnel reste le dossier frère `Audio Files` de la session; `relink_clip()` reçoit les chemins source et destination explicitement et ne les déduit pas de ces libellés internes.

Lorsque cette structure est unique, ses compteurs sont cohérents, son nombre de noms égale le nombre de `0x1003` et le `0x2629` possède exactement un segment de 104 octets, `get_timeline_clips()` retourne directement le nom à l'index `+96`.

Les layouts non standard restent lisibles par le repli nominal best-effort :

1. Le dossier frère `Audio Files` est listé pour `.wav`, `.aif` et `.aiff`, trié sans tenir compte de la casse; ses `OSError` sont absorbées.
2. Les `0x103a` sont aussi parcourus comme texte `mac_roman` afin d'en extraire des candidats audio dédupliqués.
3. Le nom virtuel perd un suffixe final `-<chiffres>` avec extension optionnelle, puis `.A1` à `.A9`.
4. Un nom de base exact unique est préféré, puis un préfixe compatible unique.
5. Zéro ou plusieurs correspondances donnent `physical_filename = None`; aucun nom n'est inventé.

Le lecteur exact n'interprète pas encore le layout alternatif de 96 octets observé sur une définition de production; cette définition utilise le repli nominal. Le relink décrit en 6.4 exige le layout standard de 104 octets.

## 6. Mutations des clips

Toutes les recherches utilisent les noms exacts. Une définition de clip dupliquée par nom ou une cible de piste/placement non unique est rejetée avant mutation.

### 6.1 Renommage, mute, déplacement et duplication

- `rename_clip()` remplace uniquement `[name_len][name]` dans l'unique `0x2628` cible, conserve toute la queue binaire, refuse les noms vides/NUL/non UTF-8 et les collisions. Tous les placements de cette définition voient donc le nouveau nom; le retour de succès vaut `1`.
- `mute_clip()` modifie `0x104f+0` sur tous les placements audio visibles de la définition cible. Les fondus, macros de groupe et timelines cachées ne sont jamais ciblés; le retour est le nombre de placements modifiés.
- `move_clip()` exige exactement un placement visible et aucune géométrie de fondu associée. Il remplace le UInt64 à `+7`, puis retrie uniquement les slots `0x1050` de la playlist en conservant les segments bruts à leur position; le retour de succès vaut `1`.
- `duplicate_clip()` exige exactement un placement de 35 octets, sans fondu associé et avec `+33 == 0`. Il clone le `0x1050`, efface ses offsets, conserve le même ID de définition, applique le nouveau timestamp et le mute demandé, insère chronologiquement l'événement et incrémente le compteur `0x1052`. L'opération est transactionnelle, ne clone aucune géométrie de fondu et retourne l'ID de définition partagé avec la source.

### 6.2 Split

`split_clip()` est transactionnel et n'accepte que les sources vérifiées `00 00 30 44 00`, `00 00 40 44 00` ou `01 00 30 44 00` aux cinq octets commençant à `A`. La longueur source est un UInt32 à `A+5`. Pour construire le fragment gauche natif, le code recopie les triplets temporels source `A+10..A+12` et `A+14..A+16`, puis leur ajoute respectivement `00` et `FF`.

Préconditions supplémentaires :

- Une seule définition portant le nom demandé.
- Une seule piste visible portant le nom demandé.
- Un seul placement audio de cette définition qui contient strictement le cut (`start < cut < end`).
- Aucun fondu associé.
- La coupe relative, qui devient à la fois la longueur gauche et l'offset source droit, tient sur UInt24.
- La longueur droite tient sur UInt32; `0x30` est utilisé jusqu'à `0xFFFFFF`, puis `0x40` au-delà.
- Le timestamp du cut stocké dans le `0x2628` droit tient sur UInt32.

Résultat :

1. Les prochains suffixes numériques disponibles sont calculés à partir du plus grand suffixe `clip-<nombre>` existant; par exemple, `-03`/`-04` suivent `-01`/`-02`.
2. Le fragment gauche utilise `01 00 30 44 08`, `src_offset=0` et une longueur UInt32 égale à la coupe relative.
3. Le fragment droit utilise le flag `0x3001` et un offset source UInt24 égal à la coupe relative. Sa longueur restante est UInt24 sous le sélecteur `0x30`, ou UInt32 sous le sélecteur `0x40`. Le flag `0x4001` est connu grâce au trim tardif, mais le split ne l'emploie pas : un split au-delà de la limite UInt24 exigerait également un layout de fragment gauche long qui n'a pas encore été observé.
4. Les deux `0x2629` sont clonés et les segments d'identité mutables de 48 octets reçoivent de nouveaux UUID; ils sont ajoutés en fin des définitions `0x262a` et son compteur augmente de deux.
5. L'événement original est muté en fragment gauche afin de conserver son offset et son pointeur `0x0002`. Un nouvel événement droit est inséré immédiatement après; le compteur `0x1052` augmente d'un.
6. La méthode retourne `(orig_clip_id, left_clip_id, right_clip_id, cut_samples)`.

### 6.3 Sous-clips et trims

`create_subclip()` crée uniquement une définition dans le Clip Bin; il ne crée pas de placement.

- La source est un ID ordinal existant.
- Un seul `0x2628` et un seul segment d'identité de 48 octets sont requis.
- Le nom UTF-8 doit être unique.
- `src_offset >= 0`, `length > 0` et les deux valeurs tiennent chacune sur UInt32.
- Seules les combinaisons observées sont produites : offset nul/longueur UInt24; offset UInt24/longueur UInt24 ou UInt32; offset UInt32/longueur UInt24.
- Un offset nul avec une longueur supérieure à `0xFFFFFF`, ou un offset et une longueur tous deux supérieurs à `0xFFFFFF`, est rejeté faute de référence Pro Tools.
- `src_offset == 0` produit `0x0001`; un offset UInt24 positif produit `0x3001`; un offset UInt32 supérieur à `0xFFFFFF` produit `0x4001`.
- Le nouveau Clip ID et un nouvel UUID sont écrits, le bloc est ajouté après le dernier `0x2629`, puis le compteur augmente.
- Le retour est le nouvel ID ordinal.

Queues exactes produites après `[name_len][name]` :

```text
src_offset == 0:
01 00 30 44 08 | length UInt32 (valeur bornée à 24 bits)
| 00×8 | FF×7 FE
| FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00

0 < src_offset <= 0xFFFFFF et length <= 0xFFFFFF:
01 30 30 44 08 | src_offset UInt24 | length UInt24
| 00×8 | FF×8
| FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00

0 < src_offset <= 0xFFFFFF et length > 0xFFFFFF:
01 30 40 44 08 | src_offset UInt24 | length UInt32
| 00×8 | FF×8
| FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00

src_offset > 0xFFFFFF et length <= 0xFFFFFF:
01 40 30 44 08 | src_offset UInt32 | length UInt24
| 00×8 | FF×8
| FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00
```

Les deux champs `00×8` sont les deux timestamps UInt32, initialisés à zéro par un appel direct à `create_subclip()`. Le split utilise les modèles `01 30 30` ou `01 30 40` pour son fragment droit et remplace ces deux timestamps par le cut absolu. Son modèle gauche utilise `01 00 30 44 08`, écrit une longueur UInt32 et recopie les deux triplets temporels de la source selon la disposition spécialisée décrite en 6.2, avec les octets terminaux `00` puis `FF`.

`trim_clip_start()` et `trim_clip_end()` exigent exactement un placement audio visible de 35 octets, aucun fondu associé et `+33 == 0`. Ils créent une nouvelle définition via `create_subclip()`, puis relient uniquement ce placement au nouvel ID.

- Trim Start : ajoute le montant à `src_offset`, soustrait le montant de la longueur et avance le timestamp `0x104f` du même nombre d'échantillons. Il remplace aussi les deux timestamps UInt32 de la nouvelle définition `0x2628` par ce nouveau départ absolu; cette valeur doit donc tenir sur UInt32.
- Trim End : conserve `src_offset` et le timestamp, puis réduit uniquement la longueur.
- Le montant doit être un entier strictement positif et inférieur à la longueur.
- Les noms générés sont `-tS` ou `-tE`, puis `-02`, `-03`, etc. en cas de collision. Un trim composé cible le nom généré par le trim précédent.
- Toute erreur restaure l'arbre et `_removed_offsets`.
- Le retour est l'ID de la nouvelle définition créée.

### 6.4 Relink physique d'un placement

**Layout hérité non écrivable** : l'origine applicative d'un header `0x2106` et sa géométrie peuvent subsister après une sauvegarde ou une copie de session Pro Tools. Un nom de fichier, `Save Copy In…` ou le logiciel qui a effectué le dernier export ne prouvent donc pas la conversion vers un profil relink natif. Le préflight et les préconditions binaires ci-dessous restent déterminants. La consolidation native préalable est le contournement validé du corpus concerné; contrôler ensuite le profil obtenu, sans supposer que toute consolidation produit un layout universel. Observation détaillée : annexe A (§6.4).

`relink_clip(track_name, clip_name, placement_start_samples, new_clip_name, source_audio_path, new_audio_path, replacement_audio_path=None)` reproduit le cas comparatif Pro Tools où deux placements partagent d'abord un WAV, puis l'un d'eux référence une identité physique indépendante. Le ciblage exige une définition de nom unique et exactement un événement audio sur la piste demandée dont le timestamp UInt64 égale `placement_start_samples`; la valeur doit aussi tenir sur UInt32 pour les champs temporels des layouts relink vérifiés. Le dernier argument facultatif fournit un WAV rendu dont seul le chunk PCM `data` sera installé dans le clone.

`get_relink_write_status(track_name, clip_name, placement_start_samples)` est le préflight public strictement **en lecture seule**. Il résout le même placement exact, valide d'abord la géométrie `0x2628` que le writer accepte, puis inspecte la définition `0x2629`, son index média `+96`, l'entrée directe `0x1003` et le header brut `0x2106`, sans modifier l'arbre, le WAV ni la table `0x0002`. Une géométrie décodée mais non écrivable retourne `{"supported": false, "code": "unsupported_clip_layout"}`; une géométrie absente ou tronquée retourne `unverified_clip_layout`. Pour un virtuel utilisant un header `0x2106` autre que les longueurs natives 142/151, le résultat est `{"supported": false, "code": "premiere_virtual_media", "detail_header_length": N}`; le corpus Premiere observé retourne `N=173`. Si le catalogue ou le header ne peut pas être vérifié sans heuristique, le résultat est également `supported=false` avec un code `unverified_*`. Un résultat positif garantit que le writer ne rejettera pas la géométrie `0x2628`, mais toutes les autres préconditions de `relink_clip()` restent obligatoires.

Préconditions binaires :

- un unique `0x1004`, dont le compteur égale ses `0x1003` directs;
- un unique `0x103a` ordonné suivant le format de 5.3, avec exactement un nom par `0x1003`, au moins un nœud de queue, les compteurs de nœud consécutifs `N+1..N+K` et les deux compteurs initiaux égaux à `N+K+2` et `N+K+1`;
- des ordinaux `0x1003` one-based consécutifs;
- un enregistrement fixe de lien média de 104 octets dans le `0x2629` source, avec index UInt32 little-endian à `+96`, et un enregistrement fixe d'identité de 48 octets. Le parseur générique peut avoir découpé un `5A xx 00 00 00 00 00` fortuit comme bloc vide : `_validated_2629_fixed_records()` resérialise alors tous les items situés entre `0x2628` et `0x4403`, puis après `0x4403`, et exige exactement 48 puis 104 octets;
- soit le layout racine spécial `00 00 30 44 00`, avec deux références temporelles UInt32 identiques, soit un layout de production **natif Pro Tools** parent ou virtuel de flag `0x0000`/`0x0001`/`0x2000`/`0x2001`/`0x3000`/`0x3001`/`0x4001`, dont les deux octets de géométrie valent normalement `(high_nibble | 0x04), 0x08` puis un sélecteur de longueur vérifié. L'exception écrivable validée est `0x3000`/`0x20`/`0x44`/`0x08` (offset UInt24, longueur UInt16). Les flags `0x0000`, `0x2000` et `0x3000` sont donc aussi relinkables; leurs offsets source sont respectivement de largeur 0, UInt16 et UInt24. Le writer conserve le flag, la queue opaque et la géométrie temporelle existante. Les marqueurs Premiere observés `0x3001`/`0x30`/`0x04|0x84`/`0x08` sont décodés à la lecture seulement : aucun relink écrit n'est spécifié pour eux;
- dans le `0x1003` modèle de relink natif, un `0x1001` brut de 31 octets — réassemblé si ses octets ont fortuitement formé un faux bloc vide — et un `0x2106` de 142 ou 151 octets avec une queue de 58 octets. Les headers Premiere de 169/173 octets sont observés et préservés par le lecteur, mais leur écriture n'est pas validée;
- un fichier RIFF/WAVE little-endian possédant exactement un chunk `bext` d'au moins 412 octets, `minf` d'au moins 16, `regn` d'au moins 92 et `umid` d'au moins 24;
- une référence temporelle UInt64 à `bext+338` égale soit aux deux UInt64 à `regn+44`/`regn+52`, soit au second lorsque le premier vaut zéro dans le layout de production, et égale au UInt32 du `0x2106` physique source. La paire racine `0x2628` est toujours validée; une référence incorporée virtuelle peut être spécifique à l'application et n'est pas utilisée pour valider le WAV. Lorsque le stem `regn` se termine avant `+76`, les deux tokens UInt64 à `+76`/`+84` doivent être identiques; le layout de production à stem long ne possède pas ces champs exploitables.

Le basename source doit correspondre au nom indexé par le PTX. Si le stem UTF-8 incorporé dans `regn` correspond au stem physique, il est remplacé; une abréviation de production divergente, par exemple `Rdy` contre `Ready`, est préservée sans être interprétée comme une erreur. Le nouveau nom physique doit être absent du catalogue, le chemin de destination absent et son dossier existant. Dans l'usage normal, l'appelant construit les deux chemins à partir du dossier `Audio Files` frère du fichier PTX; les libellés de la queue `0x103a` n'interviennent jamais dans cette résolution. Le stem UTF-8 du nouveau WAV doit différer du stem source tout en ayant exactement la même longueur, afin de rester compatible avec les champs fixes de l'identité physique.

Mutation PTX :

1. Supprime transactionnellement tout événement audio visible dont l'ID est hors de la Clip List courante, en décrémentant sa playlist et en collectant ses offsets pour la purge `0x0002`; cette normalisation empêche qu'un nouvel ordinal ne réactive un événement obsolète. Clone ensuite le `0x1003` source **uniquement dans un catalogue natif vérifié**, efface tous ses offsets, lui attribue l'ordinal suivant et synchronise ses identifiants `0x1001`/`0x2106` avec le nouvel UMID du WAV. Le FILETIME à `+29` est arrondi vers le bas à la seconde et le second vaut exactement une seconde de moins; tous deux ont donc un reste nul modulo `10 000 000`. Cette règle ne décrit pas les catalogues Premiere.
2. Incrémente les deux compteurs d'en-tête et tous les compteurs de nœud de la queue `0x103a`, y insère un enregistrement conservant le suffixe `EVAW` ou nul du catalogue, incrémente le compteur `0x1004` et place le clone après le dernier `0x1003`.
3. Clone le `0x2629`, normalise ses deux enregistrements fixes, génère un nouvel ID ordinal à `identity+0` et un UUID aux octets `identity+23..+38` — l'octet sentinelle `+22` est préservé —, remplace le nom `0x2628`, puis écrit le nouvel index physique UInt32 à `media_link+96`. La nouvelle référence média, dans le PTX et dans le WAV, vaut strictement `placement_start_samples − src_offset`; une racine réécrit sa paire temporelle au placement, tandis qu'un virtuel conserve intégralement sa queue et sa référence incorporée.
4. Incrémente `0x262a`, insère le clone après la dernière définition et remplace uniquement l'ID UInt32 à `0x104f+2` de l'événement ciblé.
5. Aucun enregistrement `0x0002` n'est ajouté : les neuf nouveaux blocs ont des offsets neufs et la référence Pro Tools comparative conserve une table `0x0002` de taille identique.

Mutation WAV :

- le fichier complet est copié vers un temporaire dans le dossier de destination; par défaut, le chunk PCM `data` reste identique;
- dans la référence d'origine de 32 octets de `bext`, seuls les quatre octets ASCII `+294..+297` (`bext+288+6..9`) sont renouvelés, conformément à la comparaison native; le reste est préservé. `bext` reçoit aussi la date/heure locale, une référence temporelle égale à `placement_start_samples − src_offset` et le nouvel UMID; `minf` reçoit le FILETIME exact non arrondi;
- `regn` préserve son mode de références : `(new,new)` dans le layout court et `(0,new)` dans le layout de production. Les deux tokens opaques sont renouvelés seulement lorsqu'ils existent avant le stem; le stem est remplacé seulement lorsqu'il concordait avec le nom physique. `regn` et `umid` reçoivent l'identifiant compact concordant;
- si `replacement_audio_path` est fourni, son RIFF doit posséder un unique `fmt ` d'au moins 16 octets et un unique `data`. Seuls PCM `0x0001` et WAVE_EXTENSIBLE `0xFFFE` avec sous-format PCM `1` sont admis. Canaux, fréquence, byte rate, block align, bits par échantillon et taille exacte de `data` doivent égaler le clone; les octets PCM sont copiés par blocs de 1 Mio sans remplacer les autres chunks;
- le remplacement final est atomique et ne peut écraser un fichier existant.

La mutation de l'arbre PTX est transactionnelle. Une erreur avant le remplacement final supprime le temporaire et restaure l'arbre. Après succès, le WAV existe déjà mais la session n'est encore qu'en mémoire : l'appelant doit exécuter `save()` et nettoyer le WAV lui-même si cette sauvegarde ultérieure échoue. Le retour est un dictionnaire contenant le nouvel ID de clip, son nom, l'index physique et le nom du WAV.

### 6.5 Construction d'une session audio depuis un template

`build_audio_session(template_ptx_path, clip_specs, output_session_directory, session_name=None)` produit un dossier autonome contenant un PTX et son sous-dossier `Audio Files`. Cette fonction de module est le seul authoring de session complet exposé; elle ne construit pas une session à partir de zéro, mais remplace et clone les structures d'un template Pro Tools strictement validé.

L'API est volontairement indépendante des conventions métier des applications clientes. Elle ne déduit ni piste, ni ordre, ni regroupement à partir des noms de fichiers. Le manifeste ordonné `clip_specs` constitue l'unique source de vérité pour ces décisions.

#### Contrat du template

Le template doit respecter simultanément les invariants suivants :

- session little-endian, `sample_rate == 48 000` et `frame_rate_enum == 0x09` (23,976 non-drop);
- au moins une playlist visible; tous les noms de pistes visibles sont uniques et l'arbre entier est dépourvu de `0x1050`, y compris dans les timelines cachées;
- exactement une définition directe `0x2629` sous l'unique `0x262a`, sans placement, utilisant l'un des deux profils d'import natifs ci-dessous;
- exactement un média direct `0x1003` sous l'unique catalogue `0x1004`, un seul filename physique ordonné et un lien média de 104 octets dont l'index UInt32 à `+96` vaut zéro;
- un seul `0x1001` et un seul `0x2106` correspondant exactement au même profil;
- un catalogue de noms `0x103a` conforme à 5.3, avec suffixe `EVAW` ou nul et au moins un nœud hiérarchique.

Les profils sont stricts; un mélange de leurs longueurs, préfixes ou offsets est rejeté. `ProToolsSession.validate_audio_import_template()` applique ce diagnostic sans modifier la session et retourne `{"profile": <nom>, "tracks": [<pistes visibles>]}`. `build_audio_session()` sélectionne ce même profil avant d'inspecter les WAV, afin que la borne de durée corresponde à la géométrie réellement écrite.

| Profil | Parent `0x2628` | Identité `0x1001` | Détail `0x2106` |
|---|---|---|---|
| `native_float_15_142` | Préfixe `00 00 30 04 00`; durée UInt24 à `+5`; référence UInt32 à `+8`. | 15 octets : 48 kHz `+0`, mono `+4`, 32 bits `+5`, durée UInt24 `+6`, cinq zéros `+9..+13`, float `0x03` `+14`. | Header 142 + queue 58; FILETIME `+29`, référence `+91`, FILETIME précédent `+96`, UUID `+126..+141`. |
| `native_float_31_151_u32` | Préfixe `00 00 40 44 00`; durée UInt32 à `+5`; deux références UInt32 à `+9` et `+13`. | 31 octets : 48 kHz `+0`, mono `+4`, 32 bits `+5`, durée UInt32 `+6`, quatre zéros `+10..+13`, float `0x03` `+14`; la queue opaque `+15..+30` est préservée. | Header 151 + queue 58; FILETIME `+29`, référence `+100`, FILETIME précédent `+105`, UUID `+135..+150`. |

Le builder ne crée, ne supprime et ne renomme aucune piste. Chaque piste demandée par le manifeste doit correspondre exactement à une piste visible existante. Les pistes du template qui ne sont pas utilisées restent présentes et vides. Le nombre de pistes du template et le nombre de pistes ciblées ne sont pas limités à une valeur métier particulière.

#### Manifeste ordonné et contrat des WAV

`clip_specs` est un itérable non vide de mappings. L'objet lui-même ne peut être une chaîne, un chemin, des octets ou un mapping unique. Chaque descripteur contient :

| Champ | Présence | Contrat |
|---|---|---|
| `audio_path` | requis | Chemin vers le WAV source. |
| `track_name` | requis | Nom exact, UTF-8, non vide et sans NUL d'une piste visible du template. |
| `physical_filename` | optionnel | Basename `.wav` utilisé dans `Audio Files` et les catalogues PTX; par défaut, basename de `audio_path`. Aucun chemin n'est permis. |
| `clip_name` | optionnel | Nom UTF-8 dans la Clip List; par défaut, stem de `physical_filename` suivi de `.A1`. |
| `placement_start_samples` | optionnel | Timestamp absolu UInt64 du placement; par défaut, référence temporelle BWF du WAV. Les booléens ne sont pas des entiers acceptables. |

L'ordre d'itération est conservé exactement dans le manifeste retourné, les catalogues média, la Clip List et l'ajout des événements. Les basenames physiques et les noms de clips doivent être uniques sans tenir compte de la casse. Une application cliente peut donc choisir librement son tri, ses regroupements et sa stratégie d'affectation des pistes avant l'appel.

Chaque source doit être un RIFF/WAVE little-endian. Le format audio accepté est strictement WAVE_EXTENSIBLE `0xFFFE`, sous-format IEEE float `00000003-0000-0010-8000-00AA00389B71`, mono, 48 kHz, 32 bits, `byte_rate=192 000`, `block_align=4` et `valid_bits=32`. Un seul `fmt ` d'au moins 40 octets, un seul `fact` d'au moins 4 octets, un seul `data` non vide et un seul `bext` d'au moins 412 octets sont requis. Le compteur UInt32 de `fact` doit égaler `data_size / 4`; cette durée doit tenir dans la largeur sélectionnée par le profil (UInt24 ou UInt32). La référence temporelle UInt64 à `bext+338` doit tenir sur UInt32. Les 32 premiers octets du basic UMID à `bext+348` ne peuvent être tous nuls.

Des événements d'une même piste peuvent se chevaucher. Chaque nouveau `0x1050` est ajouté après ceux déjà créés sur cette piste, dans l'ordre des descripteurs; un événement ultérieur est donc le dernier dans la priorité de spotting. Aucun trim, crossfade, mixage, déplacement ou changement automatique de piste n'est appliqué. Le format créé est un `0x1050` de `block_type=0x03`, contenant un `0x104f` de 35 octets (`block_type=0x0a`), ID UInt32 à `+2`, timestamp UInt64 à `+7`, type `0x03` à `+15`, constante `FE FF 00 00 00 00` à `+16`, huit `FF` à `+22`, puis la queue secondaire `00 01 01`. Lors du premier placement, le trailer brut `01 00` encore concaténé au header de la playlist vide est séparé et replacé après les événements comme dans la référence native.

`placement_start_samples` ne réécrit pas l'identité temporelle du média. La référence BWF demeure encodée dans `0x2106` et dans le parent `0x2628`; seul le timestamp UInt64 du placement `0x104f` reçoit la valeur explicite. Ainsi, un même fichier garde son identité BWF même lorsqu'une application choisit de le placer ailleurs dans la timeline.

#### Mutation des catalogues et identités

Le média et la définition prototypes sont réutilisés pour le premier fichier; les suivants sont clonés avec tous leurs `original_offset` effacés. Pour l'ordinal zéro-based `i` :

- le `0x1003` reçoit l'ordinal one-based `i+1`; son `0x1001+6` reçoit la durée dans la largeur (UInt24 ou UInt32) du profil;
- le header `0x2106` reçoit un FILETIME courant à `+29`, la référence temporelle BWF UInt32, un second FILETIME une seconde plus tôt et un UUID aux offsets du profil; la queue 58 octets reçoit toujours le basic UMID à `+18..+49`;
- la queue `0x2106+18..+49` reçoit les 32 octets du basic UMID BWF;
- le `0x2629` reçoit l'ID `i`, un nouvel UUID à `identity+23..+38`, l'index média UInt32 `i` à `media_link+96`, le `clip_name`, la durée et la ou les références temporelles aux offsets du profil;
- les enregistrements fixes `identity` et `media_link` peuvent avoir été scindés par un faux bloc vide fortuit. Ils sont réassemblés puis normalisés à exactement 48 et 104 octets. Le span `media_link`, situé après l'identité, doit être remplacé en premier; normaliser d'abord l'identité déplacerait les indices d'items et pourrait concaténer l'ancien et le nouveau lien en un record invalide de 208 octets;
- les compteurs `0x1004` et `0x262a` deviennent `N`; les `N` entrées sont contiguës et dans l'ordre du manifeste;
- tous les filename records `0x103a` sont reconstruits avec les `physical_filename` dans l'ordre du manifeste, en conservant le suffixe du prototype. Pour `N` médias et `K` nœuds, les compteurs sont recalculés directement à `N+K+2`, `N+K+1` et `N+1..N+K`;
- chaque playlist ciblée reçoit ses événements dans l'ordre relatif du manifeste. Aucun enregistrement `0x0002` n'est ajouté pour les nouveaux blocs, conformément aux comparaisons natives.

#### Transaction de fichiers et retour

Le dossier parent de `output_session_directory` doit exister et le dossier cible doit être absent, y compris sous forme de lien cassé. Le nom de session optionnel est un basename sans séparateur ni NUL; l'extension `.ptx` est ajoutée si nécessaire. Sans nom explicite, le basename du dossier cible est utilisé.

La fonction valide toutes les sources, tous les descripteurs et le modèle, puis mute le modèle en mémoire avant de créer un dossier temporaire frère. Elle y crée `Audio Files`, copie chaque WAV avec `shutil.copyfile()` sans modifier un seul octet, sauvegarde le PTX, le recharge, puis vérifie les pistes ciblées, le catalogue, la Clip List, les placements, les records fixes 48/104 octets, les IDs de définition et chaque index média UInt32. Le dossier complet est publié par un seul `os.replace()` vers une destination encore absente. Une erreur supprime seulement le dossier temporaire exact; aucun fichier existant n'est écrasé.

Le builder ne génère ni `DGDA`, ni `minf`, ni `regn`. Les sources doivent déjà fournir les métadonnées BWF/UMID requises. Les WAV sont copiés byte-for-byte, sans synthèse de chunks ni conversion audio. Les vérifications de sortie utilisent les données du manifeste courant (IDs/index, durées et placements), pas un nombre de fichiers ou un overlap fixe. Validation native des profils : annexe A (§6.5).

Le retour est un dictionnaire compatible JSON : `session_path`, `audio_files_directory`, `track_count`, la liste `tracks` des noms distincts ciblés dans l'ordre de première utilisation et la liste `clips`. Chaque clip donne son ordre, ses chemins source/destination, son filename physique, son nom de Clip List, sa piste, sa référence BWF, son début de placement, sa longueur et sa fin en échantillons.

### 6.6 Suppression d'un placement audio (`delete_clip`), validé non publié

`delete_clip(clip_name)` supprime le placement principal, **pas** la définition
du clip, son gain, son média ou son fichier physique. Ouverture, sauvegardes
natives et réouverture des deux sorties API confirmées. Les oracles/cycles et
saves sont décrits en A.35; aucune extension de profil depuis cette clôture.

Préconditions : nom str non vide, UTF-8 strict, sans NUL; little-endian;
définition `0x262a/0x2629` unique par nom et compteurs valides; exactement un
événement audio principal de cette définition, distingué des macros de groupe
par la queue secondaire. Les maps/playlists/événements sont validés via les
helpers communs. Payload cible `0x104f` de 35 octets, `+33 == 0`, début UInt64
à `+7`, longueur positive depuis la définition et fin dans UInt64. Un nom
absent, un clip non placé ou plusieurs placements lèvent une erreur : ce
contrat n'est pas le no-op/retour zéro de `delete_marker()`.

Les bindings du dictionnaire global `0x2630` restent stricts (un ID par fade,
autant d'événements que de géométries). L'association de bord utilise les
ancres début/fin et les tailles natives connues. Dans la playlist cible, une
taille inconnue ou une ancre intérieure non réassociée interdit l'opération,
plutôt que d'être interprétée comme une absence de fade. Sans fade attaché,
les événements et géométries étrangers restent inchangés, IDs compris.

Le seul cas de suppression avec fade accepté est **un fade-out UInt24 de
29 octets**, type2/`0x262f`, segment brut unique, durée positive répétée à
`+8/+11`, sélecteur `0x33` à `+5` et mode `0x02` à `+14`. Sa durée doit tenir
dans le clip; son ancre est la fin du clip et un seul audio de cette playlist
doit finir à cette ancre. Son `0x104f` mesure35, `+33 == 0`, queue `01 01 01`.
Il doit être l'unique géométrie globale; root `0x2630` type1 avec exactement
`[compteur brut4, géométrie]`. Retirer un fade parmi plusieurs géométries,
un autre profil attaché ou un crossfade est refusé, faute d'oracle de
compaction/conversion du fade voisin. Aucun ordinal sparse n'est introduit.

Toutes les préparations précèdent les affectations :

1. Retirer l'audio `0x1050` et, dans le cas admis, le `0x1050` de fade-out.
   Décrémenter le compteur UInt32 de playlist à `4+longueur_nom` du nombre
   réel d'événements retirés. Garder tous les autres items/segments et leur
   ordre; lors de la sérialisation, les bruts adjacents fusionnent naturellement
   dans la forme vide `[nom, compteur0, trailer opaque]`.
2. Si le fade est supprimé, retirer aussi sa géométrie et remplacer les items
   de `0x2630` par un seul compteur brut4 nul. Ne pas garder une géométrie
   orpheline comme dans le writer contrib examiné.
3. Collecter récursivement anciens offsets/types des événements, descendants
   et géométrie retirés dans des copies des métadonnées de suppression.
   En présence d'offsets originaux, exiger une table `0x0002` unique, au moins
   deux octets et records valides; refuser tout conflit de type déjà enregistré.
4. Affecter playlist/dictionnaire/métadonnées seulement après ces validations.
   `save()` purge/relocalise les références avec le pipeline existant; aucune
   sauvegarde ni édition de fichier audio implicite.

Retour : nombre d'événements timeline retirés, `1` (audio seul) ou `2`
(audio+fade-out). Les définitions/bin/gain/catalogues, pistes, objets/positions
survivants et champs opaques ne sont ni supprimés ni renumérotés. Les IDs de
macros de groupe numériquement égaux à l'ID audio ne sont pas des cibles.

### 6.7 Trims en place (`trim_clip_head` / `trim_clip_tail`), profil validé non publié

Ces deux méthodes prennent `clip_name` et `cut_samples`, **position absolue
session en échantillons**, entier non booléen dans UInt32. Ce n'est ni un
montant de trim, ni un offset WAV, ni une durée TC. Le cut doit être strictement
intérieur au placement; head conserve `[cut,end)`, tail conserve `[start,cut)`.
Retour : ID ordinal existant de la définition, qui ne change pas. Appeler
`save()` séparément; aucun fichier audio n'est modifié/créé.

Le helper `_trim_clip_inplace()` valide nom exact UTF-8 non vide/sans NUL,
little-endian, définition unique par nom, maps/playlists valides et un seul
placement audio principal (macro de groupe exclue). Tout `0x1050` hors des
événements principaux identifiés interdit l'écriture : pas de modification
indirecte d'une timeline cachée/alternative. Payload événement35, `+33=0`,
conteneur mutable liste. Bindings globaux de fades validés; **aucun fade ne
peut être présent dans la playlist cible**, même non attaché. Les autres
playlists ne sont pas éditées. Unknown/orphelin/partage de géométrie refusé
par le validateur commun; aucun fade retiré ou laissé orphelin.

Profil `0x2628` strict : `block_type=4`, liste d'un seul segment brut, nom
exact, taille `A+64`, header `01 10 30 44 08`, avec `A=4+name_length` :

| Offset | Champ |
|---|---|
| `A+5` | Offset source UInt8, strictement positif. |
| `A+6` | Longueur UInt24. |
| `A+9`, `A+13` | Deux UInt32 définis, chacun égal au début actuel du placement. |
| `A+17` | Queue exacte47 octets ci-dessous; index Clip Gain -1, aucun gain. |

```text
FF×8 FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00
```

Sentinelle, timestamps déplacés/différents, gain, queue UUID moderne, source
zéro, autre flag/largeur/marqueur/type/queue : refus explicite. Il ne s'agit
pas d'une interprétation universelle des deux timestamps. Le writer contrib
qui réécrivait inconditionnellement la paire avec le start n'est pas repris.

- Head : `delta=cut-start`; `new_offset=old_offset+delta` doit rester UInt8,
  sinon refus, pas de changement de largeur. `new_length=end-cut`; source,
  longueur et les deux timestamps deviennent respectivement new_offset,
  new_length et cut. UInt64 de l'événement `+7` devient cut.
- Tail : `new_length=cut-start`; **seule la longueur** change. Source, début,
  timestamps et objet payload événement restent identiques.

Définition et événement nouveaux sont préparés entièrement avant les
affectations : un échec d'encodage du second payload ne modifie aucun objet.
Nom, UUID/ID, parents, compteurs, catalogues, gain, autres événements et
métadonnées offsets/types supprimés sont conservés. Aucune allocation de
backup dans la bin ni purge/compaction TOC; tailles de blocs inchangées.
Le head natif observé conserve, lui, une copie supplémentaire de l'ancien
clip avec le même nom : ce backup est intentionnellement absent du contrat
en place. Une source avec noms homonymes demeure refusée, même si une seule
de ces définitions est placée. Comparaisons et saves Pro Tools validés : A.36.
`trim_clip_start/end`, `_trim_clip` et
`create_subclip` historiques restent AST-identiques au HEAD de référence.

### 6.8 Conservation de plages (`keep_clip_regions`), non publié, profil validé

`keep_clip_regions(clip_name, regions)` garde les paires `(start,end)` absolues
session en samples. Retour : nombre de fragments; `save()` séparé. Paires
entières non booléennes, non vides, UInt32, `start < end`, triées par la méthode,
non chevauchantes et incluses dans le placement d'origine (ses bords autorisés).
Paires adjacentes restent distinctes, aucun merge automatique. Un intervalle
égal à tout le placement retourne1 sans mutation après validation du profil.

Nom UTF-8 exact non vide/sans NUL, little-endian, définition et placement
audio principal uniques; groupes macros exclus de l'espace d'ID audio.
Maps, compteurs et bindings de fades validés. Aucun 0x1050 caché/alternatif,
aucun fade sur la playlist cible (même non attaché), aucun autre placement
audio chevauchant le clip source. Événement type3, unique 104f/type10/raw35,
octet33=0, tail000101; tous les conteneurs mutés doivent être des listes.
Placement complet `0 <= start < end <= UInt32`.

Liste 262a : raw compteur4 puis définitions2629 contiguës, sans trailer ajouté.
Cible 2629/type11 exactement : 2628/type4/raw unique, identité raw48,
opaque4403/type1 natif et lien raw104. Préfixe/suffixe de l'identité et
structure opaque/media-link exacts aux références, octet95 du lien=1,
ses quatre derniers octets nuls; champs opaques intermédiaires et index
média copiés sans modification. UUID situé à raw48+23..+38 (octet22 FF).
Nom exact; seuls headers `01 10 20 44 08`, `01 10 30 44 08`,
`01 30 20 44 08`, `01 30 30 44 08` : sourceUInt8/UInt24 positive,
longueurUInt16/UInt24, suivies de deux UInt32 définis égaux au placement
et de la queue47 exacte sans gain de §6.7. Taille totale exacte, aucune
sentinelle, UUID audio, source0/2/4, longueur1/4 ou header inconnu accepté.

Pour chaque plage : `source=old_source+start-old_start`, `length=end-start`,
tous calculés depuis le prototype **non modifié**. Source et longueur <=0xffffff;
source encodée UInt8 si <=255 sinon UInt24, longueur UInt16 si <=65535 sinon
UInt24. Une largeur vérifiée peut conserver une valeur inférieure à son
minimum d'encodage usuel; pas de troncature ni de nouveau layout deviné.
Les deux timestamps deviennent start, événement UInt64+7=start. Le premier
conserve définition/nom/ordinal/UUID; chaque suivant reçoit un ordinal à la
fin de262a et le nom `nom_source#start_samples`, refus si déjà existant.
Clone d'identité48 avec UUID frais23..38, collision UUID refusée; clone
opaque4403/raw104/event sans autre réécriture et offsets originaux effacés.

Tous les inputs/profils/noms/payloads/UUID/clones/compteurs sont préparés avant
la première affectation. Un échec tardif laisse les objets et métadonnées
intacts; pas de rollback deepcopy remplaçant les objets de l'appelant.
Compteurs UInt32 bin/playlist ajustés, événements supplémentaires après le
premier, ordre relatif des autres événements inchangé. Pas de définitions
des morceaux supprimés, purge/ajout de record TOC, save implicite, changement
WAV/media/parent/gain/fade ou metadata de suppression. Comparaisons, candidat
validé et save Pro Tools : A.37; trois fragments/autres tailles/cadences et
compositions supplémentaires sont des tests automatisés, pas des ouvertures
manuelles distinctes revendiquées.

### 6.9 Ajout d'un média natif PCM24 (`import_audio`), non publié, profil validé

Signature publique : `import_audio(track_name, audio_path, start_samples,
prototype_clip_name, clip_name=None) -> clip_id`. Elle ajoute un fichier complet
à une piste nommée existante depuis un parent explicitement choisi; aucun choix
heuristique du dernier clip. Le client fournit le WAV déjà sous **Audio Files**
de la session et gère lui-même sa copie/déplacement. Aucune écriture de fichier,
conversion, extraction ZIP, création de piste ou sauvegarde implicite.

Préconditions : session little-endian48k/enum09, piste unique nommée et playlist
à événements audio directs35octets/104ftype10/tail000101; fades/macros/autres
variantes refusés. Noms non vides/NUL-free/UTF-8, pas de doublon casefold de nom
clip ou fichier. Un seul bin audio direct cohérent; catalogue média natif31
contigu avec filenameEVAW et compteurs/trailer corroborés. Parent type11,
payload2628type4, attributsA+55/header0000304400, raw48/metadata4403/raw104
stricts; durée/BWF/indexmédia doivent correspondre au donneur31/151/58.

Le WAV est inspecté sans lire les samples : RIFF/WAVE little-endian/taille et
chunks bornés/uniques, fmt40 tag1/mono48k/byte_rate144000/block_align3/bits24
avec extension nulle; un data non vide aligné3. bext>=412, minf>=16,
regn>=92, umid>=24. UMID32 préfixeSMPTE natif et suffixe non nul; regn
longueurUInt64+28=data/3, stemUTF8+61/longueur+60=basename sans extension,
deux référencesUInt64+44/+52=BWFUInt64+338. Identifiant9octets=`2a`+UMID[16:24]
doit apparaître dansregn+11 etumid+7. BWF doit tenirUInt32, duréeUInt24
(max16777215), start/end de placementUInt64 sans overflow. Aucun identifiant
WAV/UMID déjà enregistré ni UUID nul/dupliqué/collidant accepté.

Écriture sur clones, avec profil distinct `_PCM24_AUDIO_IMPORT_PROFILE` :
ajouter le filename avanttrailer et incrémenter tous ses compteurs; cloner
un média31/151/58, ordinal1003=N+1, durée1001+6/3, identifiant+22/9,
filetimes2106+29/+105, BWF+100, UUID+135/16, UMIDtail+18/32. Cloner le parent,
nom/durée/BWFpairA+8/+12 adaptés, slotA+36..47 mis à zéro conformément à
l'import natif; autres octets opaques conservés. Raw48 premierUInt32=indexmédia
**et non ordinalbin**, UUID+23/16; raw104 indexUInt32+96=indexmédia. Nouveau
placementID=ordinalbin, UInt64+7=start, tail000101, compteursbin/playlist/catalogue
incrémentés. Aucun recordTOC supplémentaire : pipeline save existant relocalise
les anciens records et sérialise les nouveaux blocs sans offset historique.

Tout contrôle, encodage, clone, compteur, UUID et préparation d'événement
précède quatre affectations finales; les événements existants restent les
mêmes objets. Aucun remplacement global d'arbre sur refus, aucun ancienclip/
média/gain/removal-metadata modifié. RetournouvelIDbin, pas nombre d'importations.
Imports successifs et noms Unicode sont testés; variantesfmt16/extensible,
float/stéréo/multicanaux, autresprofils/cadences, sous-clips arbitraires,
arbrefilesystemgénérique et importMXF ne sont pas couverts. Builderfloat32 §6.5
reste strict et séparé. Validationnative/saveProTools A.38;18tests writer.

## 7. Fondus et crossfades

### 7.1 Dictionnaire `0x2630`

Une opération de fondu exige exactement une racine `0x2630`. Son premier segment commence par un compteur UInt32 égal au nombre de `0x262f` directs, chacun possédant un payload brut.

Chaque événement Fade (`0x104f[15] = 0x01`) utilise son UInt32 à `+2` comme index ordinal de `0x262f`. Le nombre d'événements Fade doit égaler le nombre de géométries; chaque index doit être valide et utilisé une seule fois.

| Géométrie | Taille | Champs lus/écrits |
|---|---:|---|
| Fade In | 22 | Discriminateur `0x20` à `+5`; longueur UInt16 à `+8`; mode créé `0x03` à `+10`; forme à `+11`. L'ancre de l'événement est le début du fade et doit correspondre au début d'un placement audio unique. |
| Fade Out | 26 ou 27 en lecture; 27 en création | Discriminateur créé `0x22` à `+5`; longueur UInt16 à `+8`; la création la répète à `+10`, écrit le mode `0x02` à `+12` et la forme à `+13`. L'ancre est la fin du fade et doit correspondre à la fin d'un placement unique. |
| Fade Out UInt24 (non publié, lecture seule) | 29 | Discriminateur requis `0x33` à `+5`; longueur UInt24 LE à `+8`, répétée à `+11`; les deux valeurs doivent être égales et positives. Mode requis `0x02` à `+14`. Les autres octets, dont la forme à `+15`, sont opaques et préservés. L'ancre est la fin du fade, associée à la fin d'un placement audio unique. |
| Crossfade | 34 | Discriminateur créé `0x22` à `+5`; pré-roll UInt16 à `+8`; durée totale UInt16 à `+10`; mode `0x01` à `+12`; Equal Power `0x01` à `+13`. L'ancre est le cut. |

Une autre longueur de payload est rejetée. Pour la lecture d'un crossfade, l'audio droit commençant à l'ancre est préféré; l'audio gauche finissant à l'ancre sert de repli. L'association doit rester unique.

Le chemin local non publié `_decode_fade_out_length()` conserve le décodage historique UInt16 pour 26/27 octets et lit uniquement le nouveau profil vérifié 29/`0x33`/`0x02`. Un profil de 29 octets à largeur ou mode différent, deux durées divergentes ou une durée nulle est rejeté sans mutation. Le début calculé `anchor - duration` ne peut pas être négatif. Aucune déduction générique depuis les nibbles ni fallback par longueur n'est ajouté pour les autres tailles; le fade-out compact de 25 octets décrit dans la contribution reste non vérifié et refusé.

Le lecteur utilise les durées exactes stockées dans le PTX, pas une durée nominale déduite du timecode affiché : l'arrondi des labels ne modifie pas le nombre d'échantillons. Les valeurs concrètes aux différentes cadences et les sauvegardes natives du profil UInt24 sont documentées en annexe A (§7.1).

La reconnaissance d'un fade-out attaché inclut aussi la taille 29 pour les garde-fous de move/duplicate/split/trims et le refus d'un fade-out déjà présent à la même ancre. Elle n'autorise aucune modification de ce fondu. `add_fade()` et `add_crossfade()` restent limités à leurs profils UInt16 historiques, même si le lecteur peut maintenant lire ce fade-out natif UInt24.

### 7.2 Fade autonome

`add_fade()` prend une ancre absolue et une durée. Une durée convertie à zéro sélectionne une seconde (`sample_rate` échantillons). La durée finale doit tenir sur UInt16, donc être au plus `65 535` échantillons.

- Types : `in` et `out`, sans tenir compte de la casse.
- Formes : `power = 0x01` et `linear = 0x02`.
- Fade In : l'ancre peut être au début du placement mais pas à sa fin; le fade s'étend vers la droite et doit rester dans le clip.
- Fade Out : l'ancre peut être à la fin du placement mais pas à son début; le fade s'étend vers la gauche et doit rester dans le clip.
- Le chemin d'écriture accepte techniquement une ancre intérieure. Le lecteur `get_timeline_clips()` ne peut toutefois réassocier un Fade In que si son ancre égale le début du placement, et un Fade Out que si elle égale sa fin. Pour une session relisible par toute l'API, les fades autonomes doivent donc rester des fades de bord.
- La combinaison piste, définition et ancre doit résoudre un seul placement.
- Un fondu équivalent déjà lié à la même piste/ancre est refusé.
- La nouvelle géométrie est ajoutée à la fin des géométries, tandis que le `0x1050` est inséré chronologiquement : avant un audio au même timestamp pour un Fade In, après pour un Fade Out.
- Le `0x104f` créé mesure 35 octets, reste actif à `+0`, référence le nouvel index de géométrie à `+2` et n'active pas le lien crossfade à `+33`.
- L'opération est transactionnelle.
- Le retour est l'index ordinal de la nouvelle géométrie.

Pour tous les fondus créés, les octets `0x104f[15:35]` partent du modèle `01 FE FF 00 00 00 00 FF FF FF FF FF FF FF FF 00 00 00 00 00`; le crossfade remplace ensuite `+33` par `0x01`.

### 7.3 Crossfade centré

`add_crossfade()` convertit une durée strictement positive, au plus `65 535` échantillons, valide `0x2630`, puis appelle `split_clip()` dans une transaction englobante.

- La géométrie de 34 octets utilise `pre_roll = floor(duration / 2)` et la durée totale demandée. Pour une durée impaire, l'échantillon excédentaire se trouve donc du côté droit.
- Le mode et la courbe sont toujours Equal Power (`0x01`, `0x01`).
- Le fondu est inséré immédiatement avant le fragment droit.
- Le `+33` du fondu et celui du fragment droit passent à `0x01`; le mute du fondu reste `0x00`.
- Les limites et refus du split s'appliquent intégralement.
- Le retour normal est `None`.

Les fondus existants peuvent être lus et de nouveaux fondus peuvent être ajoutés, mais aucune API ne permet de déplacer ou remodeler une géométrie existante. Le candidat `delete_clip()` §6.6 retire seulement le fade-out UInt24 unique avec son clip; il ne fournit pas une suppression indépendante/générale de fade. Move, duplicate, split et trims refusent les placements auxquels un fondu est attaché.

`0x2077` n'est pas un cache de fondu : il appartient aux marqueurs. Aucun `0x2077` n'est nécessaire pour le calcul audio d'un fade.

## 8. Marqueurs

`0x2030` est générique. Un bloc racine est reconnu comme règle de marqueurs seulement s'il possède l'une des deux dispositions suivantes :

- Vide : un seul segment brut de 12 octets, dont le UInt32 initial vaut zéro.
- Peuplé : un segment initial de 4 octets contenant le compteur UInt32, uniquement des `0x2077` directs, puis un trailer brut de 8 octets. Le compteur doit égaler le nombre de marqueurs.

Le premier segment brut de chaque `0x2077` lu par l'API possède :

| Offset | Taille | Champ |
|---|---:|---|
| `+0` | 2 | Index UInt16. |
| `+2` | 4 | Octets structuraux; le modèle créé utilise `03 09 00 00`. |
| `+6` | 4 | Longueur du nom UInt32. |
| `+10` | variable | Nom UTF-8. |
| `+10+N` | 8 | Premier timestamp Int64; utilisé par `get_markers()`. |
| `+18+N` | 8 | Second timestamp Int64; `add_marker()` écrit la même valeur pour créer un point. |

`get_markers()` exige des index uniques dans toutes les règles reconnues et convertit le premier timestamp en timecode. Sans règle reconnue ou sans marqueur, il retourne `[]`.

### 8.1 Catalogue des règles de markers et filtre de lecture

Le profil natif multi-règle vérifié contient une unique racine `0x2519`, un enfant direct `0x251b`, puis des entrées directes `0x251c`. Une entrée de règle a le payload exact suivant :

```text
UInt16LE ruler_id
UInt32LE name_length
UTF-8 name[name_length]
0x01
UInt32LE ruler_id
```

Les entrées `0x251c` qui n’ont pas cette forme sont des entrées UI non-règle et sont ignorées. Les `ruler_id` et noms de règle doivent être uniques. Le dernier segment brut de huit octets d’un événement marker `0x2077` est `UInt32LE(0) | UInt32LE(ruler_id)`.

`get_markers(marker_track_name=None, *, include_comments=False)` reste entièrement en lecture seule. Sans argument, il retourne tous les marqueurs reconnus. Avec un nom de règle, il valide le catalogue ci-dessus et l’assignation de **chaque** marker, puis retourne uniquement ceux dont l’ordinal final correspond au nom demandé. Un catalogue absent, ambigu, invalide, un nom inconnu ou une assignation inconnue est refusé par `ValueError`; aucun repli par position ou par nom de marker n’est autorisé. `include_comments` est un Boolean strict; le champ `comment` n'est ajouté aux markers retournés que sur opt-in, selon §8.3. Les commentaires des markers écartés par le filtre ne sont pas décodés.

`add_marker(name, tc_samples, index=None, comment=None)` exige :

- Au moins une playlist principale entièrement valide.
- Exactement une règle de marqueurs reconnue.
- Un nom `str` sans NUL, encodable en UTF-8 et dont la taille tient sur UInt32.
- Un timestamp entier entre `0` et `2^63-1`.
- Un index explicite unique entre `1` et `65 535`, ou `max(index existant)+1`.
- Pour l'extension non publiée de §8.3 : commentaire `None` ou `str` UTF-8 sans NUL, longueur en octets UInt32; `None` équivaut à la chaîne vide.

Le marqueur créé dérive d'un modèle natif contenant `0x2506`, deux `0x4826`, un `0x4827` et des segments bruts. Le lecteur ne prétend pas que ces sous-blocs sont obligatoires dans toutes les révisions; ils décrivent seulement le modèle écrit par l'API. Un nouvel UUID RFC 4122 est injecté aux octets `23..38` du segment secondaire du `0x2077`, puis tous les offsets du modèle sont effacés avant insertion.

`add_marker()` retourne l'index effectivement écrit.

**Drapeau de population legacy, correction non publiée validée en import direct** :
quand le catalogue optionnel est présent, `_prepare_legacy_marker_population_flag()`
résout l'unique `0x2519`, son unique `0x251b` direct (block_type1), puis l'entrée
zéro `0x251c` directe (block_type2). Le compteur brut du catalogue est exactement
UInt32 et égale le nombre de records directs; aucun autre item n'est accepté.
Chaque record doit avoir un payload brut unique et un ID UInt16 lisible. La cible
zéro est exactement 11 octets : `[UInt16 id=0][UInt32 name_len=0][Boolean][UInt32 id=0]`.
Seul **+6** change à1 lors d'un ajout, à0 après suppression du dernier marqueur,
à1 s'il en reste. Ce champ est corroboré par les trois oracles natifs de suppression
et leurs saves; les autres records/flags restent intacts. La préparation du
payload précède toute mutation d'ajout/suppression. Un no-match de suppression
ne répare pas le flag. Catalogue absent (pas de0x2519 ou pas de0x251b) : ancien
contrat conservé, sans garantie d'import. Présent mais ambigu/inconnu : refus.
Ce helper ne modifie ni les noms de règles ni les affectations des marqueurs et
ne prétend pas étendre l'écriture aux autres profils de catalogue.

Le profil moderne du contributeur à deux `0x2506` n'est pas intégré. Les
commentaires sont adaptés séparément au profil historique selon §8.3, dont le
writer conserve un seul `0x2506`. Huit
régressions supplémentaires vérifient transitions, préservation/atomicité,
cycles/restauration exacte et le résultat natif d'un import direct.
L'essai Import Session Data dans Pro Tools 2024.3.1, sans ouverture/resauvegarde
préalable de la source API, conserve le nom et les deux timestamps égaux du
marqueur. La source API demeure byte-identique. La destination sauvegardée
normalise le `0x2077` : premier brut de 201 octets, aucun `0x2506`, deux `0x4826`,
un `0x4827` et brut final d'affectation de 8 octets. Cette forme est conservée
par deux cycles API exacts et ses 159 records TOC standards restent valides.
Cette observation ne définit pas un writer compact ou ses champs de commentaire;
la présence/absence ou le nombre de `0x2506` ne suffit pas à identifier ce champ.

L'API crée des marqueurs ponctuels seulement; elle ne modifie pas les marqueurs existants et ne crée pas de sélection ou de propriétés avancées de Memory Location. La suppression non publiée ci-dessous possède un contrat et une validation native distincts.

### 8.2 Suppression de markers ponctuels legacy — non publiée, validée dans Pro Tools

Signature : `delete_marker(index=None, name=None)`. Au moins un critère est
requis : index entier non booléen 1..65535; name `str` UTF-8 sans NUL, y compris
nom vide si réellement présent. Les deux critères combinés sont un **ET**.
Nom exact, sans strip/casse/fuzzy matching; un même nom peut supprimer plusieurs
points aux index distincts. Retour nombre supprimé, zéro sans mutation si absent.
La règle vide reconnue retourne également zéro, sans modifier ses objets.

Profil little-endian : une seule règle reconnue racine `0x2030`, type 5,
disposition/count/trailer de §8. Toute règle ambiguë ou `0x2077` imbriqué sous
une racine `0x2030` non reconnue est refusé. Les autres conteneurs génériques
non-marker restent opaques. Tous les markers directs sont validés avant filtre,
même un point non ciblé ou une recherche sans résultat. Index globaux uniques.

`_validated_deletable_point_marker()` exige type `0x12`/contenu `0x2077`,
exactement sept items dans l'ordre suivant :

| Item | Forme requise |
|---|---|
| 0 | Brut `136 + N` octets : index UInt16 non nul +0, `03 09 00 00` +2, longueur N UInt32 +6, nom UTF-8 sans NUL +10; Int64 à +10+N et +18+N identiques et non négatifs. Les 110 octets suivants restent opaques. |
| 1 | Bloc type3/`0x2506`, unique brut 8 octets; contenu opaque conservé. |
| 2 | Brut 51 octets : vingt zéros, `00 ff ff`, UUID opaque 16 octets, douze ff. |
| 3 et 4 | Deux blocs type1/`0x4826`, chacun unique brut 7 octets; UI opaque. |
| 5 | Bloc type1/`0x4827`, unique brut 28 octets; contenu opaque. |
| 6 | Brut 8 zéros, assignment réservé0/ordinal0 du profil legacy corroboré. |

Ce profil ne reprend pas le reader permissif de la contribution. Ranges,
forme moderne compacte sans `0x2506`, autre assignment et géométries inconnues
sont refusés, de même qu'un commentaire non vide (la longueur primaire/UUID
sort alors de cette géométrie fixe). La lecture multi-règle §8.1 et les
commentaires §8.3 n'élargissent pas ce writer. Le corps
opaque n'est ni interprété ni normalisé; aucun UUID de survivant régénéré.

Si des points restent : `[UInt32(len(keep)), *keep, trailer8]`; sinon
`[UInt32(0) + trailer8]`, brut12, en **conservant les huit octets** plutôt que
les remplacer systématiquement par zéro. Objets/ordre/index/positions des
survivants inchangés, aucune renumérotation ni conversion TC/samples.

Préparer tous les nouveaux items et des copies des métadonnées avant mutation.
Collecter offsets originaux positifs et types du marker et de chaque descendant;
si des offsets doivent être purgés, exiger une unique table `0x0002` dont les
records standards sont valides. Conflit avec un type supprimé déjà enregistré
refusé. Puis remplacer les items de la règle et affecter les métadonnées prêtes.
`save()` réutilise la purge TOC avant/après relocalisation existante; ne pas
rechercher le marker par timestamp/nom dans la table brute. Aucun fichier écrit
par delete_marker, pas de save implicite ni modification audio/piste/média.

Comparaison native non vide/vide et survivants corroborée, six cycles API des
trois oracles exacts; sorties API et tests de références construites vérifiés.
**Ouverture/lecture/save/réouverture des deux sorties API confirmées.**
Les sauvegardes natives conservent les survivants exacts et passent les cycles
API/compositions de suppression des derniers points; étape31 clôturée.
Preuves et divergences natives opaques : annexe A.34. Pas de support publié
sur la seule base d'un rechargement par l'API.

### 8.3 Commentaires de markers ponctuels legacy — non publiés, validés dans Pro Tools

Une paire native corrobore deux champs UTF-8 préfixés UInt32LE. Noter `N` la
longueur en octets du nom et `C` celle du commentaire. Le modèle est un `0x2077`
type0x12, exactement sept items : brut primaire, `0x2506` type3/brut8, brut
secondaire, deux `0x4826` type1/brut7 chacun, `0x4827` type1/brut28, assignment
brut8 avec UInt32 réservé zéro. Aucun scan de signature dans un brut.

| Champ | Offset / géométrie |
|---|---|
| Primaire : nom et deux timestamps | En-tête §8; `03 09 00 00` à+2; timestamps non négatifs et égaux. |
| Primaire : longueur du commentaire UInt32LE | `26 + N + 60`, soit **+60 après les deux timestamps**. |
| Primaire : texte UTF-8 | `30 + N + 60`, exactement `C` octets. Taille totale du brut `136 + N + C`;46 octets opaques après le texte. |
| Secondaire : réservé | Quatre zéros à+0. |
| Secondaire : longueur du commentaire UInt32LE | +4, doit égaler `C`. |
| Secondaire : texte UTF-8 | +8, exactement `C` octets; doit être byte-identique à la copie primaire. |
| Secondaire : enveloppe suivant le texte | Treize zéros, `ff ff`, UUID opaque16, douze `ff`. Taille totale `51 + C`. UUID à `23 + C`. |

`_decode_legacy_marker_comment()` exige cette géométrie, contrôle toutes les
bounds/tailles avant lecture des champs variables, compare les copies puis
décode UTF-8 strict sans NUL. Il ne requiert pas l'égalité des bytes UI/UUID aux
valeurs d'une fixture. Une forme compacte, à deux propriétés, un range ou un
miroir incohérent est une erreur explicite sur opt-in; pas de fallback vers `""`.
Chaîne réellement vide valide : deux longueurs zéro. Pas de limite artificielle
0xffff : le champ est UInt32; le test automatisé au-delà de UInt16 ne valide
pas une éventuelle limite d'interface Pro Tools.

`add_marker(..., comment=None)` encode le texte avant toute mutation. Sur son
prototype legacy, il remplace les deux champs longueur+texte, conserve tous
les préfixes/suffixes opaques et le nouvel UUID déjà préparé, puis valide le
marqueur complet avant effacement des offsets et préparation du drapeau de
population. Texte vide/None garde les anciens payloads. Aucun save implicite,
aucune modification de commentaire existant, aucune extension de suppression.

La paire native avant/après donne `C=42`, croissance primaire et secondaire
de42 octets chacun; préfixes/suffixes intacts sauf UUID secondaire régénéré par
Pro Tools. D'autres racines opaques changent pendant ce save natif : elles ne
sont pas attribuées au commentaire ni normalisées par le writer. Reconstruction
API du marker identique à l'oracle, sauf son UUID frais; deux cycles exacts par
oracle et sortie API. Treize régressions ajoutées, suite617 réussie avec -W error.
**Ouverture/affichage exact du commentaire/save/réouverture de la sortie API
confirmés par l'utilisateur**. La sauvegarde native conserve les deux points,
les commentaires vide/Unicode et les deux timestamps égaux de chaque point.
Elle garde un `0x2506` par marker. Une quatorzième régression native optionnelle
vérifie cette sauvegarde, ses métadonnées et deux cycles API byte-identiques;
suite complète618 tests réussis. Le profil moderne du contributeur utilise un autre offset (+61)
et n'est pas assimilé à ce profil validé par comparaison.

## 9. Clip Gain statique

### 9.1 Profil historique entièrement statique

Le writer exige une racine `0x2637` plate unique; dans le profil entièrement statique :

```text
point_count UInt32 LE | point 0 (30 octets) | ... | point N-1
```

Chaque point contient les 26 octets de métadonnées vérifiés ci-dessous et une valeur Float32 LE finie aux octets `26..29`. La taille du payload entièrement statique est exactement `4 + 30 × point_count`. Le nouveau validateur `_validated_clip_gain_dictionary()` vérifie chaque record, pas seulement cette taille totale.

Dans chaque `0x2628`, l'index signé Int32 à `len(payload)-6` vaut `-1` sans Clip Gain ou référence un point existant. Tous les index de toutes les définitions sont validés avant écriture.

`set_clip_gain()` :

- Cible une définition de clip au nom unique; toutes ses occurrences partagent donc le gain statique.
- Accepte, sauf les booléens, toute valeur convertible par `float()` vers un réel fini, ainsi que `-math.inf`, `"-inf"` ou `"-infinity"`; `NaN` et `+inf` sont rejetés.
- Remplace `-inf` par la sentinelle Pro Tools Float32 `-290.2105712890625`, octets LE `f4 1a 91 c3`.
- Si l'index vaut `-1`, ajoute un point avec les 26 octets de métadonnées `01 46 01 00 16 00 00 00 00 00 01 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00`.
- Si plusieurs définitions partagent le point, clone les 30 octets et relie uniquement la cible au nouvel index.
- Sinon, remplace uniquement les quatre octets Float32 du point existant.
- Le retour est l'index du point global finalement lié à la définition.

Le header statique est exactement la séquence de 26 octets de création ci-dessus; il porte magic `01 46 01 00`, taille UInt32 0x16, padding nul, un nœud, flags `04 00`, zéro segment, réservé nul et position relative zéro. Headers non vérifiés et anciennes valeurs non finies sont désormais refusés avant mutation, y compris dans un autre record non ciblé.

Séquences Float32 LE de référence :

| Valeur | Octets LE | UInt32 équivalent |
|---:|---|---:|
| `0.0` | `00 00 00 00` | `0x00000000` |
| `-10.0` | `00 00 20 c1` | `0xc1200000` |
| `+6.0` | `00 00 c0 40` | `0x40c00000` |
| sentinelle `-inf` | `f4 1a 91 c3` | `0xc3911af4` |

L'édition des enveloppes/breakpoints de Clip Gain n'est pas prise en charge.

### 9.2 Dictionnaire mixte — modification non partagée validée, non publiée

Un dictionnaire mixte contient des records statiques et les seuls profils d'enveloppe explicitement vérifiés, dans un ordre quelconque et avec un nombre variable de records. L'API utilise le compteur et les index de la session courante; elle ne suppose ni quatre records ni trois statiques. Une sauvegarde Pro Tools peut réordonner/compacter les records et réassigner les index. Comparer alors les liens de définition, pas d'anciens ordinaux; le writer API conserve l'ordre/index existants hors ajout/clonage explicite. Exemples : annexe A (§9.2).

Chaque record commence par magic `01 46 01 00` puis taille UInt32 LE à +4. Sa longueur totale est **8 + taille déclarée**, contrairement au payload Volume de §10. Le parseur commence à +4 du dictionnaire, avance strictement par ces tailles, lit exactement count records et exige la fin exacte du payload. Aucun padding, aucun scan/find de signature et aucun recours à un stride de 30 sur un dictionnaire mixte. Une signature présente dans une valeur de nœud ne crée pas de record.

| Champ dans un record | Statique vérifié | Enveloppe conservable vérifiée |
|---|---:|---:|
| Taille totale / UInt32 à +4 | 30 / 0x16 | 54 / 0x2e |
| Padding +8..+9 | zéro | zéro |
| Nombre de nœuds UInt32 +10 | 1 | 4 |
| Flags UInt16 +14 | 4 | 4 |
| Segments UInt32 +16 | 0 | 3 |
| Réservé UInt16 +20 | zéro | zéro |
| Paires à +22, chacune 8 octets | position UInt32 0, gain Float32 | quatre positions UInt32/gains Float32 |

L'enveloppe exige une première position relative zéro, des positions strictement croissantes et des valeurs finies. Aucun terminateur après les paires. Son header exact de 22 octets est `01 46 01 00 2e 00 00 00 00 00 04 00 00 00 04 00 03 00 00 00 00 00`. Le second profil conservable, trois nœuds/0x26, est décrit en §9.5. Les autres tailles inconnues (notamment 0x1e/deux nœuds et 0x36/cinq nœuds) sont refusées par `NotImplementedError`; les incohérences/troncatures/valeurs non finies par `ValueError`.

`set_clip_gain()` valide tous les records et tous les index de toutes les définitions avant la mutation. Dans le cas d'un point statique existant non partagé, il remplace sa valeur Float32 à `record_offset+26` et conserve count, ordre, autres records et tous les index. La cible d'enveloppe est refusée sans mutation. Le cas partagé fait l'objet de §9.3, l'ajout d'un premier point (index -1) de §9.4. Le profil entièrement statique de 9.1 conserve ajout/clonage; aucune normalisation vers un autre profil.

Les positions de nœuds sont relatives au clip; les valeurs Float32 observées doivent être préservées exactement, sans les arrondir vers les valeurs nominales d'une consigne de test. Les positions/timecodes et gains concrets d'une courbe de référence ne sont pas des valeurs obligatoires du profil. Voir annexe A (§9.2).

La modification statique non partagée avec conservation de l'enveloppe est validée dans Pro Tools. Aucune détection UUID moderne ni édition d'enveloppe n'est ajoutée; les différences de métadonnées de sauvegarde native restent opaques.

### 9.3 Clonage d'un point partagé mixte — validé, non publié

Le contrôle de partage porte sur **toutes les définitions de clips**, pas seulement les occurrences visibles. Si plusieurs définitions référencent le même point statique existant, le chemin de clonage de 9.1 est réutilisé :

1. Copier le record statique complet de 30 octets depuis l'offset borné de §9.2 (pas `4 + 30 × index` dans un dictionnaire mixte).
2. Remplacer uniquement son Float32 +26..+29 dans la copie.
3. Ajouter cette copie à la fin du dictionnaire et incrémenter le count UInt32.
4. Relier seulement la définition cible au nouvel ordinal (ancien count), dans son index Int32 historique à -6.

Ancien point, ordre/bytes de tous les records existants, index de toutes les autres définitions et enveloppe restent intacts. Prévalidation/préparation complète avant affectations. L'index ajouté est borné par Int32 signé comme dans 9.1. Une cible devenue non partagée est ensuite modifiée en place, sans re-clonage ni accroissement du count. Aucun nouvel enfant PTBlock/pointeur individuel pour le record, puisque `0x2637` reste un payload brut; la sauvegarde relocalise les pointeurs standards après la croissance du bloc.

La validation du clonage partagé combine une référence native et une condition de partage construite explicitement; elle n'est pas présentée comme une nouvelle paire native avant/après. Les noms, gains, compteurs et index de ce montage sont des données d'essai uniquement (annexe A, §9.3).

Le clonage partagé est validé dans Pro Tools. Le compactage/réordonnancement effectué à la sauvegarde native n'est pas reproduit comme normalisation par l'API. Les gains restent reliés par définition; les enveloppes restent byte-identiques.

### 9.4 Premier point statique mixte — ouverture/sauvegarde native validées

Pour une cible d'index historique -1 dans un dictionnaire mixte vérifié, le writer réutilise l'ajout de 9.1 : créer le point statique de 30 octets avec ses 26 octets de métadonnées vérifiées et la valeur Float32 validée; l'ajouter à la fin, incrémenter le count et relier seulement la cible à l'ancien count. Toutes les définitions et tous les records sont prévalidés, y compris les entrées non ciblées. Aucun réordonnancement ou retrait de record, aucun effet sur l'enveloppe.

Plusieurs index -1 ne représentent pas un partage de point : la branche d'ajout prime sur le comptage de références identiques, et les autres -1 restent inchangés. Une seconde attribution à la même cible modifie son nouveau point en place, sans allocation supplémentaire. Limite Int32 signé inchangée. Les cibles d'enveloppe restent refusées, de même que les tailles de records non vérifiées; aucune édition de breakpoints ni détection UUID nouvelle.

L'ajout initial dans un dictionnaire mixte est validé par ouverture/sauvegarde Pro Tools depuis une condition d'absence construite sur référence native. Cette preuve ne transforme ni un nom de clip, ni la valeur choisie de gain, ni le compteur du montage en précondition. Le writer ne normalise pas le dictionnaire selon sa sauvegarde native. Détails : annexe A (§9.4).

### 9.5 Enveloppe trois nœuds conservable — validée dans Pro Tools

Le second profil d'enveloppe conservable contient **trois nœuds**, dans un record de **46 octets** dont la taille déclarée est **0x26**. Sa taille/arité sont des contraintes du profil; le nombre total de records du dictionnaire, les index associés, les positions/gains des nœuds et les noms des clips ne sont pas fixés par l'exemple natif. Validation : annexe A (§9.5).

Header exact de 22 octets : `01 46 01 00 26 00 00 00 00 00 03 00 00 00 04 00 02 00 00 00 00 00`. Padding +8 nul, count UInt32 +10 = 3, flags UInt16 +14 = 4, segments UInt32 +16 = 2, réservé UInt16 +20 nul. Trois paires `[position UInt32, valeur Float32]` à +22/+30/+38; pas de terminateur. La première position vaut zéro; les suivantes sont strictement croissantes, et toutes les valeurs sont finies selon §9.2. Les positions et valeurs sont conservées exactement, pas remplacées par celles d'une fixture.

Le validateur sélectionne uniquement les deux profils d'enveloppe explicitement corroborés : **0x26→3** et **0x2e→4**, avec leurs headers complets. Il n'infère pas le nombre de nœuds d'une taille inconnue et n'accepte pas un autre header de même longueur. La marche bornée par `8 + size`, le compteur exact et l'épuisement sans octets restants sont inchangés. Les tailles deux/cinq nœuds restent refusées; aucune recherche globale de magic ni queue UUID heuristique.

Le writer statique applique les mêmes règles : Float32 +26 d'un statique non partagé, clonage selon §9.3 et ajout pour index -1 selon §9.4. **Une cible d'enveloppe reste toujours refusée** : aucune création/édition de courbe. La conservation des trois nœuds pendant une modification statique indépendante est validée dans Pro Tools; les sauvegardes natives peuvent réordonner les records sans changer les liens/gains. Aucune normalisation native n'est transposée au writer. Preuves : annexe A (§9.5).

## 10. Automation de volume et panoramique

Le resolver de `get_volume_automation` / `set_volume_automation` (§10.1–10.4)
corrobore les identités live entre metadata, les deux familles `0x251a` et
le `0x2619` du chemin natif du slot. Il ne recherche pas de GUID par scan du
cache source et ne reprend pas un ordinal si ces identités sont contradictoires.
Les playlists doivent être explicitement nommées; un appel préalable à
`fill_anonymous_playlist_names()` (§4.1.9) peut matérialiser les noms sur le
profil corroboré. La composition AAF mono passe les tests d'identité/ciblage,
cycles exacts et restauration entière; son ciblage et sa sauvegarde réelle
Pro Tools sont validés (annexe A.39). Le writer historique suivant conserve son contrat distinct :
aucun élargissement implicite aux sessions anonymes/stéréo ni aux profils AAF
arbitraires n'est annoncé.

La cible du writer historique `add_volume_node()` est une définition `0x261c` :

- Chaque définition doit contenir exactement un `0x2619`; son payload est `[name_len UInt32][name UTF-8]`.
- Le nom visible validé de `0x1052` a priorité et est associé par ordinal aux `0x261c`; cette association exige le même nombre de pistes visibles et de définitions.
- Si aucun nom visible ne correspond, un nom interne `0x2619` unique sert de repli.
- La cible doit contenir exactement un `0x260d`.
- La première occurrence `0x260a` directement enfant de ce `0x260d` est la playlist de volume. Une occurrence imbriquée, notamment sous `0x260c`, ne la remplace pas.

Payload plat `0x260a` :

| Offset | Taille | Champ |
|---|---:|---|
| `+0` | 4 | Identifiant exact `01 46 01 00`. |
| `+4` | 4 | Taille déclarée = `len(payload)-10`. |
| `+8` | 2 | Padding nul. |
| `+10` | 4 | Nombre de nœuds UInt32. |
| `+14` | 8 | Flags. Le UInt32 à `+16` est le nombre de segments, soit `max(0, nodes-1)`. |
| `+22` | `6 × N` | Nœuds triés. |
| fin | 2 | Terminateur nul. |

Chaque nœud est `[timestamp UInt32][valeur Int16]`. La valeur est en déci-dB. Les timestamps existants doivent être strictement croissants et uniques; un ajout au même timestamp remplace le nœud, sinon il est inséré et la liste est retriée.

`add_volume_node()` exige un timestamp `0..2^32-1` et, sauf pour les booléens, une valeur convertible par `float()` puis finie, dont le `round()` Python de `db × 10` tient sur Int16 (`-32768..32767`). Il reconstruit la taille, le compteur de nœuds, le compteur de segments et le terminateur. Il ne supprime aucun nœud, ne touche aucune autre automation et retourne le nombre total de nœuds après écriture.

### 10.1 Lecture seule du profil mono nommé (`get_volume_automation`, non publié)

`get_volume_automation(track_name=None)` lit les enveloppes de Volume sans aucune mutation. L'argument facultatif doit être `None` ou un `str` non vide, sans NUL, UTF-8 valide; le nom est exact et sensible à la casse. Sans playlist principale, l'appel non filtré retourne `[]`; une demande nommée inexistante est refusée. Les playlists principales doivent être nommées : les profils anonymes ne sont pas élargis ici. `_validated_anonymous_mono_track_names(N)` est réutilisé pour corroborer noms, ordre, compteurs et identités Audio mono via `0x1015`/`0x2107`/les deux familles `0x251a`/`0x261c`, pas pour autoriser les playlists anonymes. Les noms doivent égaler ceux de la map principale. Aucun fallback par nom interne, nombre seul ou anciens offsets de `self.data`.

`_validated_mono_volume_lanes()` exige une racine `0x2624` unique/cohérente selon ce validateur, aucun slot `0x261c` imbriqué; chaque slot est type `0x04`, sa définition directe unique `0x261b` type `0x0d`. Le conteneur principal `0x260d` est unique directement et dans tous les descendants du slot, type `0x05`, avec **exactement 13 items** :

```text
0  [0x1029/type0x0d, contenu opaque]
1  brut 01 00
2  [0x260e/type0x09, contenu opaque pour ce lecteur]
3  brut 01
4  [0x260a/type0x01 : Volume]
5  brut 01
6  [0x260a/type0x01 : autre automation, non interprétée]
7  brut 00
8  [0x260c/type0x02 : pan, contenu opaque pour ce lecteur]
9  [0x260c/type0x02 : deuxième pan, contenu opaque pour ce lecteur]
10 brut 01
11 [0x260a/type0x01 : autre automation, non interprétée]
12 brut 00 × 8
```

L'item 4 de cette géométrie est la lane Volume corroborée, sélectionnée par sa propriété et sa position dans l'état principal. Une recherche de magic/signature ou la lecture de tous les `0x260a` serait incorrecte; les items 6/11 et les pans imbriqués ne sont pas des résultats Volume. Leur contenu reste opaque; ce lecteur ne valide pas les routages ni le pan lui-même. Un autre ordre/type/sélecteur, une géométrie supplémentaire/manquante, un conteneur de state ambigu ou des identités contradictoires sont refusés.

La lane de Volume doit posséder un seul payload brut. `_decode_verified_mono_volume_payload()` exige au moins 30 octets, identifiant `01 46 01 00`, padding `+8..9` et terminateur nuls, `N >= 1`, longueur `24 + 6*N`, taille `+4 == len(payload)-10`, flags `+14..15 == 02 00`, réservé `+20..21 == 00 00`, segments `+16 == N-1`, timestamps UInt32 strictement croissants/uniques. Chaque point lu est `[UInt32LE sample][Int16LE valeur]`; `db = valeur / 10.0`, timecode calculé par `TimecodeEngine` courant. Toutes les lanes Volume du profil sont validées **avant** le filtre et le retour; aucune enveloppe tronquée/partielle ni `ok=False` de récupération n'est retournée.

Résultat frais, une entrée par piste lue dans l'ordre des slots : `{"track": nom, "ordinal": index_zero_based, "ok": True, "reason": "", "node_count": N, "payload_len": taille, "nodes": [{"sample": UInt32, "timecode": label, "db": valeur/10.0}, ...]}`. Un filtre retourne seulement l'entrée exacte, en conservant son ordinal d'origine. Listes/dictionnaires n'exposent aucun objet mutable de l'arbre. Le profil de lecture stéréo est ajouté séparément en 10.3; folders, variantes anonymes/cachées/réordonnées ou layouts alternatifs restent hors portée. Le writer mono non publié est décrit en 10.2 et n'est pas élargi par 10.3.

Le profil de lecture mono est corroboré par comparaison native. Certains champs opaques `0x1029` peuvent évoluer à la sauvegarde Pro Tools; ils ne sont pas interprétés ni synchronisés comme cache/fader par ce lecteur. Leurs offsets observés et la paire sample/dB figurent en annexe A (§10.1).

### 10.2 Remplacement/fusion — profil mono validé (`set_volume_automation`, non publié)

Signature : `set_volume_automation(track_name, nodes, replace=True, ordinal=None)`. Un seul nom exact de piste Audio mono du profil 10.1; `track_name` est une chaîne non vide, sans NUL, UTF-8 valide. `replace` est strictement booléen. L'ordinal facultatif est un entier non booléen >= 0 qui doit correspondre au slot live de cette piste; il ne permet aucun fallback de résolution.

`nodes` est un itérable de paires `(sample, db)`; chaînes/octets refusés comme paire. Samples entiers non booléens dans `0..0xffffffff`; ordre d'entrée libre, doublons entrants refusés. dB non booléen convertible par `float()`, fini; `round(db * 10)` doit rester fini et dans `-32768..32767`. L'arrondi est celui de Python (égalité vers l'entier pair), comme le writer historique. `replace=True` remplace tous les points et exige au moins un point. `False` fusionne avec l'enveloppe live : point entrant prioritaire seulement à timestamp égal, autres anciens points conservés. Fusion vide permise; résultat trié strictement par sample. Aucun effacement complet de l'enveloppe.

Validation de **toutes** les enveloppes existantes par `_validated_mono_volume_lanes()` avant traitement des entrées, même pour remplacement/no-op. Un compteur incohérent dans une autre piste n'est ni ignoré ni normalisé. Tous les paramètres, points et allocations du nouveau payload/résultat précèdent la seule mutation. Une exception de l'itérateur utilisateur est propagée sans modifier l'arbre. Aucun rollback partiel ou écriture anticipée d'une première lane.

Écriture du seul payload direct de Volume décrit en 10.1 : magic conservé `[:4]`; taille UInt32 `14 + 6*N` à +4; padding `[8:10]` conservé; compteur UInt32 N à +10; flags `[14:16]` conservés; segments UInt32 `N-1` à +16; réservé `[20:22]` conservé; points `<Ih` à +22; terminateur original de deux octets. Longueur `24 + 6*N`. Limite `N <= (0xffffffff - 26)//6`, tenant compte des deux octets content_type du bloc PTX; sinon `OverflowError`. Nouveau payload redécodé/validé avant affectation. Enveloppe identique : aucun remplacement d'objet payload. Objet lane, original_offset, autres automations, pans, routage, catalogues, `0x1029` et métadonnées de suppression restent intacts. Retour préalloué `[(ordinal, N)]`, y compris no-op. Pas de save implicite.

Le remplacement/fusion mono de ce profil est validé par ouverture/sauvegarde/réouverture Pro Tools. Seule l'enveloppe cible est modifiée; les évolutions opaques `0x1029` de la sauvegarde native ne sont pas reproduites par l'API. `add_volume_node()` reste inchangé. Détails et contrôles d'atomicité : annexe A (§10.2).

### 10.3 Lecture du Volume Audio stéréo (`get_volume_automation`, non publié)

Chaque piste Audio stéréo du profil vérifié possède deux playlists principales de même nom (canaux L/R), mais une seule lane Volume dans son état principal. Le résultat contient une entrée par slot de piste, pas par canal. L'index du premier canal d'une piste est la somme des largeurs des pistes qui la précèdent; il ne faut pas associer une lane à la playlist de même ordinal. Le nombre de pistes, leur position et leurs noms sont variables. Le format public reste celui de §10.1; l'ordinal retourné est celui du slot.

Le getter appelle `_validated_named_audio_volume_lanes()`, puis `_validated_audio_track_identities(count, allow_stereo=True)` avec le compte des descripteurs directs `0x1014`. Les racines/compteurs/descripteurs, metadata `0x210b`, deux familles `0x251a` et identité native de `0x2619` sont corroborés comme en 10.1. Le wrapper `_validated_anonymous_mono_track_names()` utilise ce même validateur avec `allow_stereo=False` : aucun support stéréo/anonyme ni writer supplémentaire n'est introduit par ce factoring.

Après chaque nom préfixé `0x1014`, mono : queue 39 octets, préfixe `00 01 00 00 00`, marqueur `2a 00 00 00` à +11, deux index UInt32 à +5/+30 égaux au **premier index de playlist de canal**. Stéréo : queue 45 octets, préfixe `01 02 00 00 00`, deux index UInt16 +5/+7 égaux à `k,k+1`, padding nul +9..12, marqueur à +13, deux index UInt32 +32/+36 égaux à `k,k+1`. Donc index de canal stéréo représentable en UInt16; les autres champs de cette queue restent opaques, aucune signification déduite pour les GUID/champs non corroborés. `k` avance de 1 (mono) ou 2 (stéréo), pas toujours d'une piste. Toute autre géométrie/largeur ou index incompatible est refusé.

Dans chaque queue de nom `0x251a` de 42 octets, les six premiers octets doivent être `[width-1] + cinq zéros` et les deux derniers `[width-1, 0]`; les deux familles doivent corroborer le nom, l'identité et l'ordre déjà exigés en mono. Le préfixe avant le nom reste `00 00`. Une largeur de miroir seule ne suffit pas : elle doit concorder avec les deux formes de descripteur ci-dessus. En session contenant une stéréo, chaque slot a un unique record brut de 12 octets `[UInt32 ordinal][01 00][UInt16 ordinal][00 00 ff ff]` cohérent avec son rang. Cette vérification ne passe pas par un nom interne périmé ou un simple compte global.

La séquence des noms de pistes est développée selon leur largeur, puis comparée exactement à **toute** la map principale nommée. Identités uniques et noms de pistes uniques obligatoires; le nom répété dans les deux playlists d'une même stéréo n'est pas une piste homonyme. `_validated_volume_state_lanes(names)` partage la géométrie stricte de 13 items et le décodeur d'enveloppe de 10.1. Ce dernier garde son nom privé `_decode_verified_mono_volume_payload` : le payload stéréo observé est identique au profil mono, pas un nouveau format supposé. Tous les états/enveloppes sont validés avant filtre/retour; seules la lecture live et des listes/dictionnaires frais sont utilisés.

Le profil de lecture stéréo est corroboré dans une session mixte avec préservation des autres automations et données. Les modifications opaques `0x1029` ne sont pas interprétées ni synchronisées. Les valeurs et la topologie concrètes de cette session sont uniquement des preuves de validation (annexe A, §10.3).

**Séparation des contrats** : l'écriture stéréo/mixte utilise §10.4. `add_volume_node()`, `get_tracks()`, les lecteurs de timeline, le resolver anonyme et le routing gardent leurs propres profils; l'élargissement du Volume ne leur est pas transmis automatiquement. Le lecteur Pan indépendant est décrit en §10.5. Pistes non Audio, largeurs >2, profils cachés/réordonnés/anonymes et états multi-lanes alternatifs restent hors de ce profil Volume.

### 10.4 Remplacement/fusion stéréo/mixte — validé dans Pro Tools, non publié

Même méthode publique/signature/règles de points que 10.2. Seul le resolver cible change : `set_volume_automation()` appelle désormais `_validated_named_audio_volume_lanes()` décrit en 10.3, pas `_validated_mono_volume_lanes()` directement. Toutes les identités/largeurs/index de playlists/ordinaux de slots, tous les états et toutes les enveloppes existantes doivent être valides **avant** les entrées et la seule affectation au payload cible. Profil stéréo inconnu ou enveloppe invalide d'une autre piste refusés même en remplacement complet. Aucun fallback par compteur, GUID périmé ou anciens offsets.

Un nom unique de piste cible **une seule lane Volume**, y compris en stéréo : aucun clone/écriture séparée de canaux L/R. `ordinal` facultatif est l'index live du slot de piste; dans mono–stéréo–mono, la dernière piste a ordinal 2 et non l'index 3 de sa playlist de canal. Retour `[(ordinal, N)]`. Le payload observé et les règles de tri/UInt32/Int16/arrondi/doublons/remplacement non vide/fusion/no-op restent ceux de 10.2. Aucune reconstruction d'état/pan/routage, nouvel offset ou métadonnée de suppression; les flags/padding vérifiés et `0x1029` restent ceux du before. Le rôle de ses deux changements natifs n'est pas déduit.

Le profil d'écriture stéréo/mixte est validé dans Pro Tools, avec préservation des pans et des autres données. Les données concrètes de la paire, cycles et tests d'atomicité sont en annexe A (§10.4).

Les modifications opaques `0x1029` effectuées par Pro Tools à la sauvegarde ne sont pas des règles de synchronisation ajoutées au writer. Le profil mono de §10.2 demeure pris en charge; aucun writer multi-cible ou éditeur d'automation général n'est revendiqué.

### 10.5 Lecture du Pan principal statique mono — non publiée, paire native corroborée

Signature : `get_track_pan(track_name)`. Nom exact `str` non vide/NUL et UTF-8
valide. Retour entier signé **-100..100** (-50 = L50, 0 = centre), ou `None`
si les deux conteneurs mono sont vides. Nom absent/ambigu et cible stéréo :
`ValueError`, pas `None`. Ce lecteur reste mono; le lecteur gauche/droite
distinct est décrit en §10.7 et le writer mono en §10.6. La lecture
d'automation mono indépendante est décrite en §10.9. Une enveloppe automatisée n'est pas ramenée à
son premier point et n'est jamais remplacée par un statique.

Le helper `_validated_mono_pan_tracks(static_target=track_name)` réutilise le resolver Audio
nommé mono/stéréo et les validations d'état/Volume 10.1/10.3. Cette dépendance
conservatrice exige aussi chaque enveloppe Volume valide. Les noms/identités,
largeurs et ordinaux de slots sont corroborés depuis l'arbre live; aucun scan
GUID dans le cache initial. Les conteneurs de **toutes les pistes mono** sont
validés avant filtre. Les slots stéréo témoins ne sont pas décodés en Pan et
ne peuvent pas être ciblés. Une mono après stéréo utilise son slot de piste,
jamais l'index de playlist de canal. Profils anonymes/cachés/non Audio ou
états inconnus restent exclus selon le resolver déjà décrit.

Dans le state principal vérifié de 13 items, les deux `0x260c` (block_type 2)
sont aux index 8/9. Pour mono : second conteneur = un raw nul de **14 octets**;
premier soit identique (aucune lane, donc `None`), soit exactement les trois
items `[raw 01, 0x260a block_type 1, raw nul de 13 octets]`. Lane = un unique
payload brut de **30 octets** : header +0..+25 exact
`0146010014000000000001000000020000000000000000000000`, puis **Int16 LE à +26**,
terminateur nul UInt16 +28. Taille déclarée +4 = 0x14, count +10 = 1, flags
+14 = 2, segments +16 = 0, réservé +20 nul et timestamp UInt32 +22 = **0**.
Tout header différent, valeur hors -100..100, lane tronquée/supplémentaire,
conteneur imbriqué, second pan mono peuplé ou automation de la cible est refusé.

Les autres monos peuvent contenir une automation Pan valide selon §10.9 :
leur enveloppe complète est décodée/validée, mais ni réduite au premier point
ni modifiée. `static_target` impose le profil statique seulement au nom ciblé;
les gardes d'identité/state/Volume/conteneurs ne sont pas contournées.

Les champs d'état UI `0x1029` ne sont ni une source de valeur Pan ni une cible de mutation pour ce lecteur. Les corrélations binaires observées dans une paire native ne prouvent pas un rôle général cache/fader ni un profil d'écriture. Offsets/valeurs de ces observations et contrôles de préservation : annexe A (§10.5).

Le profil de lecture statique mono est corroboré nativement et testé sans mutation. Les tests incluent une piste mono après des témoins stéréo et la distinction entre pan absent et pan centré; voir annexe A (§10.5). Les contrats distincts d'écriture mono et de lecture gauche/droite sont décrits en §10.6 et §10.7; ces méthodes statiques refusent l'automation de la cible, pas celle des autres pistes au schéma déjà pris en charge; lecteur de courbe mono distinct en §10.9.

### 10.6 Écriture du Pan principal statique mono — non publiée, validée dans Pro Tools

Signature : `set_track_pan(track_name, left, right=None)`. `left` doit être
un entier non booléen dans **-100..100** (-50 = L50); les floats/chaînes
ne sont pas convertis. Pour une cible mono, tout `right` autre que `None`
est refusé avant mutation; la branche stéréo distincte est décrite en §10.8.
Nom et profil de la cible suivent §10.5, via le helper commun
`_validated_static_pan_target()` (branche mono), depuis l'arbre courant et le slot
corroboré, pas un index de playlist de canal. Tous les pans mono, identités,
états et enveloppes Volume sont validés avant la sélection de la cible.

La cible doit posséder une lane statique existante vérifiée : le cas
`None` du lecteur est refusé, même pour écrire centre. Aucun ajout de lane,
effacement d'automation, écriture du canal droit ou réduction d'enveloppe.
Le writer prépare une copie du payload de 30 octets et remplace seulement
son **Int16 LE +26**; header, timestamp initial zéro et terminateur restent
exacts. Valeur identique : retour **0**, sans remplacement du payload;
valeur changée : retour **1** après un remplacement unique de `lane.items[0]`.
Tout refus précède cette mutation. Aucun `save()` implicite.

Les champs UI opaques `0x1029`, autres lanes, routages, catalogue, médias,
positions, objets de lane/offsets et métadonnées de suppression restent
intacts. La validation native confirme que ce profil fonctionne sans
synchroniser les champs UI corrélés; elle ne définit pas une règle générale
pour toutes les géométries d'état. Automation de la cible, profils anonymes,
cachés/réordonnés, non Audio et états inconnus restent exclus. Preuves et
données de validation : annexe A.27.

### 10.7 Lecture du Pan principal statique gauche/droite — non publiée, paire native corroborée

Signature : `get_track_pan_lr(track_name)`. Nom exact non vide/NUL et UTF-8
valide, validé par `_validate_pan_track_name()` partagé avec §10.5/10.6.
Retour tuple **`(left, right)`** pour une piste Audio stéréo, **`(pan, None)`**
pour une mono à lane statique et **`(None, None)`** pour le profil mono
vérifié sans lane. Chaque valeur présente est un entier signé -100..100.
Nom absent/ambigu ou profil inconnu : `ValueError`, jamais une paire inventée.

Le helper mono §10.5 corrobore d'abord identités/largeurs, slots, états,
Volume et pans mono. Le lecteur décode ensuite **toutes les pistes stéréo**
avant de sélectionner le nom : une autre piste stéréo malformée bloque
aussi un résultat filtré mono. Une piste stéréo occupe deux playlists de
canal mais un seul slot de piste; la sélection de l'état utilise cet ordinal
corroboré, sans catalogue count-only, GUID scan ou cache `self.data`.

Dans l'état principal de 13 items, `state.items[8]` contient le Pan gauche,
`state.items[9]` le droit. Chacun doit être un `0x260c` block_type 2 avec
exactement `[raw 01, 0x260a block_type 1, raw nul de 13 octets]`. Chaque lane
doit contenir un seul payload brut de 30 octets, header +0..+25 et
terminateur +28 exacts de §10.5; valeur Int16 LE +26, timestamp UInt32 +22
zéro, count 1, flags 2, segments 0. `_decode_verified_static_stereo_pan_container()`
valide chaque conteneur et sa valeur -100..100. Lane absente, géométrie
inconnue, payload scindé/imbriqué, header différent ou automation de la cible : refus,
pas de défaut L100/R100 déduit, pas de réduction au premier point.

Aucune mutation, lecture/écriture UI, cache, sauvegarde implicite ou écriture
par ce lecteur. `get_track_pan()` et la branche mono de `set_track_pan()`
gardent leurs profils existants : ils n'utilisent pas ce décodeur pour imposer
de nouvelles conditions aux témoins stéréo. Les dépendances strictes d'identité/Volume et
les exclusions anonymes/cachées/réordonnées/non Audio restent celles du
resolver partagé. Preuves : annexe A.28.

Le contrat courant passe `static_target=track_name` aux deux helpers de
résolution statique. Les autres stéréo peuvent porter deux courbes valides
selon §10.11, décodées avec `allow_automation=True` et conservées intactes.
Les deux lanes de la cible stéréo restent obligatoirement statiques; une
autre lane malformée ou absente bloque toujours ce lecteur, même ciblant une mono.

### 10.8 Écriture du Pan principal statique stéréo — non publiée, validée dans Pro Tools

Même signature publique `set_track_pan(track_name, left, right=None)`.
La branche stéréo exige deux lanes existantes au profil complet §10.7.
Chaque valeur fournie est un entier non booléen -100..100; `right=None`
conserve la valeur droite existante, pas une valeur centrée inventée.
Le helper `_validated_static_pan_target(..., allow_stereo=True)` corrobore
nom, identités/largeurs, slots, state/Volume et tous les pans mono, puis
`_validated_static_audio_pan_values(tracks, static_target=track_name)` décode tous les pans stéréo
avant toute écriture. Une autre stéréo malformée bloque même un no-op.
Pour une cible mono, cette seconde validation stéréo n'est pas appelée :
§10.6 garde sa portée conservative; le lecteur mono reste mono seulement.

Résoudre les deux lanes depuis `state.items[8:10]` du slot corroboré.
Pour chaque valeur changée, copier le payload de 30 octets, remplacer
seulement Int16 LE +26 et préparer les bytes définitifs. **Toutes les
préparations doivent réussir avant la première affectation** à `lane.items[0]`.
Un échec sur le canal droit ne doit pas laisser la gauche modifiée.
Lanes/headers/timestamps/terminateurs, UI opaque `0x1029`, routages,
médias, autres automations et métadonnées de suppression restent intacts.
Chaque payload identique conserve son objet; retour **0** si aucun canal
ne change, **1** sinon (compte de piste, pas de canaux). Save séparé.
Aucune création de lane ni réduction d'automation, pas de synchronisation
UI déduite des miroirs natifs. Profils non Audio, anonymes/cachés/réordonnés,
largeurs >2 et autres géométries non corroborées restent hors portée.
Validation réelle ouverture/lecture/sauvegarde/réouverture et contrôles de
conservation/compositions : annexe A.29. Ce writer statique refuse l'automation de la cible;
lecture de courbe mono séparée : §10.9.

Comme pour §10.7, `_validated_static_audio_pan_values(tracks,
static_target=track_name)` accepte/valide l'automation des autres stéréo,
sans en exposer un faux résultat statique ni en changer un octet. L'écriture
reste limitée aux Int16 +26 des lanes statiques ciblées. Les conteneurs et
payloads non ciblés gardent leurs objets; aucune géométrie nouvelle n'est
écrite. Huit régressions de coexistence vérifient lecture sans mutation,
slots mixtes, refus sans mutation, no-op et différence strictement limitée
aux champs ciblés après save/relecture/restauration sur références natives.
L'une des deux bases est une composition explicite par le writer d'automation
déjà validé; ce n'est pas une nouvelle paire avant/après native Pro Tools.

Contrôles après cet ajustement : 596 tests locaux avec `-W error`, 469 exécutés
et 127 skips explicites sans fixtures privées, syntaxe Python 3.8 de 57 fichiers
et Pyflakes propres. Aucun site `raise` ajouté/supprimé : le catalogue §13
conserve ses 984 sites, avec la portée des refus Pan statiques mise à jour.

### 10.9 Lecture d'automation du Pan principal mono — non publiée, paire native corroborée

Signature : `get_pan_automation(track_name, right=False)`. Nom exact non
vide/NUL et UTF-8 valide selon `_validate_pan_track_name()`. right exige
un booléen; pour une cible mono il doit être False. La branche stéréo
distincte est décrite en §10.11. Retour **liste fraîche de tuples (sample, pan)**,
samples absolus UInt32, valeurs Int16 signées -100..100. Une lane statique
renvoie [(0, valeur)]; deux conteneurs mono vides vérifiés renvoient **None**,
pas un point centré inventé. Nom absent/ambigu ou canal droit mono : ValueError.

`_validated_mono_pan_tracks(allow_automation=True)` garde le resolver Audio
nommé mono/stéréo et la validation entière des états/Volume §10.1/10.3.
Les conteneurs mono conservent la géométrie stricte §10.5; les conteneurs
stéréo témoins restent opaques et ne sont pas ciblables. Toutes les lanes
Pan mono présentes sont décodées avant sélection de la cible, même celles
d'autres pistes. Un slot représente une piste, pas une playlist de canal.
Pas de scan GUID/magic, anciens offsets ou UI utilisée pour les valeurs.

Décodeur indépendant `_decode_verified_mono_pan_payload(payload)` :

- Magic +0 `01 46 01 00`, taille minimale 30, padding +8 UInt16 zéro.
- Count UInt32 +10 **N > 0**, taille totale exactement **24 + 6×N**,
  taille déclarée UInt32 +4 exactement `len(payload)-10`.
- Flags UInt16 +14 exactement 2, segments UInt32 +16 exactement N-1,
  réservé UInt16 +20 zéro, terminateur final de deux zéros.
- N records `[sample UInt32 LE, pan Int16 LE]` de 6 octets à +22+6×i.
  Premier timestamp zéro; suivants strictement croissants et uniques.
  Toutes les valeurs -100..100, pas seulement le premier point.

La géométrie comptée est lue sans limite arbitraire au nombre de nœuds,
mais sans accepter d'autre header/flag, octets supplémentaires, records
tronqués ou timestamp initial non nul. Formes natives corroborées : statique
N=1 et courbe N=3; autres arités de ce même schéma couvertes par tests
synthétiques, pas présentées comme nouvelles comparaisons natives.
Lecture live uniquement, aucun payload/UI modifié, ni save implicite,
normalisation ou liste partielle. Le mode statique par défaut du helper
ne change pas : `get_track_pan()`, `get_track_pan_lr()` et `set_track_pan()`
continuent à refuser une enveloppe au lieu d'en prendre le premier point
ou de l'écraser. Les exclusions anonymes/cachées/réordonnées/non Audio et
états inconnus restent celles du resolver commun. Preuves : annexe A.30.
Le resolver `_validated_pan_automation_target()` est partagé avec le writer
mono §10.10 après validation du nom par l'appelant. Le même resolver couvre
désormais la lecture stéréo §10.11 et l'écriture stéréo §10.12; l'ancien
paramètre privé allow_stereo a été retiré après validation native.

### 10.10 Remplacement d'automation Pan principal mono — non publié, validé dans Pro Tools

Signature : `set_pan_automation(track_name, nodes, right=False)`. Même
contrat de nom et de cible mono que §10.9; right booléen False obligatoire.
Exiger une lane existante, vide/absence refusée : aucun conteneur créé.
Le resolver commun valide toutes les identités/états/Volume et courbes mono
existantes avant la sélection, même pour un remplacement intégral/no-op.
Témoins stéréo opaques pour une cible mono; extension stéréo distincte §10.12.

nodes : iterable **non vide** de paires `(sample, pan)`, ni paire chaîne
ou octets ni longueur autre que deux. sample entier non booléen UInt32,
pan entier non booléen -100..100; aucun arrondi ou coercition float/chaîne.
Rejeter les timestamps dupliqués entrants, trier l'entrée entièrement et
exiger premier timestamp zéro selon le décodeur §10.9. Itérateur interrompu
ou erreur tardive propagés sans mutation. Remplacement complet uniquement,
pas de fusion ou modification multi-cible. Une liste d'un seul point est
un remplacement statique **explicite**, pas un collapse automatique par
`set_track_pan()`, dont le contrat reste inchangé.

Pour N points, garde-fou `N <= (0xffffffff - 26)//6` pour la taille de bloc
UInt32; sinon OverflowError. Construire le header depuis la lane vérifiée :
magic +0..3 et padding +8..9 conservés; size UInt32 +4 = 14+6N, count
UInt32 +10 = N; flags +14..15 et réservé +20..21 conservés, segments UInt32
+16 = N-1. Corps `[sample UInt32, pan Int16]` à +22+6i et terminateur
conservé. Revalider le payload complet avec le décodeur §10.9 **avant**
un unique remplacement de `lane.items[0]`. Payload identique : conserver
son objet. Retour **N**, même pour une écriture identique, pas un booléen
ou le nombre de pistes. Aucun save implicite ni compensation de position.

Objet lane/offsets/métadonnées de suppression, autres automations,
conteneurs Pan, champs UI opaques, routage, clips, médias et placements
conservés. Les nombres de points sont variables dans ce schéma vérifié;
preuve native d'écriture N=3, N=1 natif statique et autres arités couvertes
par tests synthétiques, pas annoncées comme comparaisons natives distinctes.
L'ouverture/lecture/sauvegarde/réouverture du candidat est confirmée;
positions réellement éditées du save natif conservées exactement en cycles
et compositions. Preuves : annexe A.31. Lecture et écriture stéréo sont
corroborées séparément en §10.11 et §10.12.

### 10.11 Lecture d'automation Pan principal stéréo — non publiée, paire native corroborée

Même `get_pan_automation(track_name, right=False)`; right booléen strict,
False sélectionne la gauche d'une stéréo, True la droite. Retour liste
fraîche (sample absolu UInt32, valeur Pan Int16 -100..100), sans interpréter
les valeurs UI. Pour une cible mono, §10.9 reste inchangé et un canal droit
est refusé. Absence vérifiée mono = None; aucune absence stéréo acceptée
comme valeur de défaut : les **deux lanes doivent exister**.

`_validated_pan_automation_target(track_name, right=False)` commence par
le resolver d'identités Audio/state/Volume et de toutes les courbes mono
§10.9. Une cible stéréo déclenche ensuite la validation des deux conteneurs
de **chaque piste stéréo** avant de retourner le canal sélectionné; une
autre piste ou le canal non demandé malformé bloque également le résultat.
Une cible mono ne fait pas cette validation stéréo, donc les témoins restent
opaques selon son contrat antérieur. Ordinal du slot de piste corroboré,
jamais index de playlist de canal, GUID scan ou cache de source.

State de 13 items vérifié : positions 8 et 9 pour gauche/droite. Géométrie
de conteneur §10.7 inchangée : type 2/0x260c, exactement raw 01, lane directe
type 1/0x260a avec unique payload brut, suffixe de 13 zéros. Le helper
historique `_decode_verified_static_stereo_pan_container()` possède
désormais allow_automation=False par défaut. En mode True il valide cette
géométrie puis appelle `_decode_verified_mono_pan_payload()` : **même
schéma complet natif que §10.9**, tailles/count/flags/segments/reservés/
terminateur, premier timestamp zéro/ordre strict et toutes valeurs vérifiées.
Son nom/message interne mono d'enveloppe reste historique, pas une autre
formule ou un autre namespace. En mode False, le profil statique exact de
30 octets est inchangé : méthodes statiques toujours sans collapse.

Profil natif de trois nœuds corroboré sur les deux canaux avec des positions
finales différentes; lecture sans limite de N dans ce schéma compté vérifié,
autres arités testées synthétiquement, pas revendiquées comme nouvelles
comparaisons natives. Aucune mutation/UI/cache/save/normalisation ni liste
partielle. Preuves : annexe A.32. Le writer partage désormais cette résolution
après validation native distincte de §10.12/annexe A.33.

### 10.12 Remplacement d'automation Pan principal stéréo — non publié, validé dans Pro Tools

`set_pan_automation(track_name, nodes, right=False)` étend §10.10 aux pistes
Audio stéréo nommées du profil corroboré. right est un booléen strict : False
sélectionne mono/gauche, True sélectionne la droite d'une stéréo. True sur
mono est refusé, aucune lane absente/unknown créée ou valeur implicite inventée.
Le resolver commun §10.11 valide les deux conteneurs de chaque piste stéréo
pour une cible stéréo, ainsi que toutes les identités/états/Volume et courbes
mono; une autre piste ou le canal non ciblé malformé empêche même un no-op.
Les témoins stéréo restent opaques pour une cible mono.

Contrat d'entrée/tri/limites/count/encodage/no-op de §10.10 inchangé. Même
schéma d'enveloppe natif §10.9 sur les deux canaux : un seul payload préparé
et revalidé avant une affectation sur la lane sélectionnée. Objet lane,
offsets et métadonnées conservés; aucune UI/normalisation de timestamp ni
save implicite. Un appel modifie un canal; deux appels puis save permettent
de modifier les deux, mais ne constituent pas une transaction multi-canal.
Les méthodes statiques ne sont pas assouplies et refusent toujours les courbes.

Writer public reproduisant byte-identique le candidat à deux courbes ouvert,
sauvegardé et rouvert dans Pro Tools. Sauvegarde native aux timestamps exacts,
cycles/compositions réversibles, autres audio/médias/références conservés.
Preuve native N=3 par canal; N=1 statique natif, autres arités du schéma
compté couvertes synthétiquement. Pas de généralisation à tout profil PTX.
Sept régressions supplémentaires; preuves en annexe A.33.

## 11. Clip Groups

La lecture des définitions est indépendante des clips audio : `0x262c` contient un compteur UInt32 et des `0x262b`; l'ID de groupe est leur ordinal. Les macros de timeline utilisent ce namespace, pas celui de `0x262a`. Le lecteur `get_timeline_clip_groups()` accepte autant de définitions et de macros visibles que la session en contient. Les écrivains restent intentionnellement plus étroits : `delete_clip_group()` dissout le profil simple historique, `create_clip_group()` convertit une région audio native existante à partir d'un prototype de template, et `create_empty_clip_group()` écrit le profil natif vide décrit en 11.2.

### 11.1 Création template-driven (`create_clip_group`)

La signature publique est `create_clip_group(track_name, group_name, start_samples, prototype_group_name)`. Elle ne crée ni média ni définition audio : elle convertit **une** occurrence audio visible existante (`0x1050` / `0x104f`, type `0x03`, queue `00 01 01` ou `01 01 01`) sur la piste et au timestamp demandés. Sa longueur est celle de la définition audio ciblée. Les applications clientes restent donc responsables de fournir la région source dans une session compatible, sans convention de filename ou de commentaire imposée par l'API.

Le profil d'écriture validé est le groupe audio simple mono/composant suivant :

- Racines uniques `0x262c`, `0x2424`, `0x2426`, `0x2428` et timeline principale `0x1054`; leurs listes de groupes `0x262b`, noms `0x2423`, métadonnées `0x2425` et pistes internes `0x1052` ont toutes le même compteur UInt32.
- `prototype_group_name` désigne une définition existante et exactement une macro visible `00 00 01`; le prototype interne correspondant est une `0x1052` **vide** (`count=0`, queue `01 00`). Le groupe nouvellement produit a donc une seule piste et un seul événement interne.
- Le nouveau groupe reçoit l'ordinal `N` (ancien compteur). Il est ajouté aux quatre listes parallèles. Son nom UTF-8 doit être absent de toute la liste `0x262b`, pas seulement des macros déjà placées. Le trailer 9 octets de `0x262b` porte l'ID à l'octet 1; le profil observé limite donc la création à `N <= 255`.
- Le `0x2628` du prototype est cloné. Sa longueur complète est réécrite à `A+10`, avec sélecteur indépendant `A+2` (`0x10/0x20/0x30/0x40`) choisi selon la largeur minimale de la longueur cible. Les deux UInt32 qui suivent immédiatement cette longueur deviennent `start_samples`. Les octets inconnus sont conservés. Dans le profil validé, le UInt32 opaque à `A+6` est l'origine de coordonnées de la playlist interne; il est recopié comme UInt64 à `+7` de l'événement audio caché, et **n'est ni zéro ni le timestamp visible**.
- Le payload `0x2523` cloné reçoit le nouveau début à `+0`, le nouvel ID à `+16`, la fin `start + length` à `+44`, et sa valeur à `+36` est ajustée par la différence de longueur par rapport au prototype. Les autres octets restent ceux du prototype.
- Le `0x2423` cloné porte `[group_id UInt32][name_length UInt32][UTF-8][queue conservée]`; le `0x2425` parallèle porte le nouvel ID UInt32 à `+5`.
- L'événement audio source est déplacé (cloné puis remplacé) dans la nouvelle `0x1052` cachée avec queue audio conservée et timestamp interne décrit ci-dessus. Dans la piste visible, son clone devient la macro : ID `N` à `payload+2`, `start_samples` UInt64 à `payload+7`, flag obligatoire `payload+18 = 1`, queue `00 00 01`. Omettre ce flag produit un groupe présent dans la Clip List mais absent de la timeline.
- Trois enregistrements standard de `0x0002` (nom `0x2423`, métadonnée `0x2425`, piste cachée `0x1052`) sont ajoutés dans les séries respectives des trois prototypes. Les nouveaux blocs reçoivent des offsets synthétiques uniques seulement pour cette relocalisation; `save()` les remplace par leurs offsets sérialisés. Les définitions `0x262b` et événements `0x1050` ajoutés ne reçoivent pas d'enregistrement supplémentaire dans le profil observé.

Toutes les validations précèdent la mutation et l'arbre est restauré transactionnellement si une étape échoue. `start_samples` et `start_samples + length` doivent tenir dans UInt32, bien que la macro visible utilise un UInt64. Les groupes imbriqués, plusieurs composants/événements/pistes internes, fades internes, prototype placé plusieurs fois, région source absente ou ambiguë, et toute queue ou compteur non observé sont refusés.

Le profil audio-backed est validé dans Pro Tools sur des durées et pistes distinctes. Les exemples ne fixent ni noms de groupes/pistes ni durée unique; seules les préconditions ci-dessus limitent la création. Preuves : annexe A (§11.1).

### 11.2 Création de groupes vides (`create_empty_clip_group`)

La signature publique est `create_empty_clip_group(track_name, group_name, start_samples, length_samples)`. Elle n'exige ni région audio visible, ni média, ni prototype de groupe : elle crée directement un Audio Region Group vide de durée positive. Elle est réservée au profil natif explicitement observé dans une session vierge à 48 kHz / enum `0x09` (23.976). Toute autre fréquence ou cadence est rejetée avant mutation.

Le profil écrit est le suivant :

- Les racines uniques `0x262c`, `0x2424`, `0x2426`, `0x2428` et la timeline principale `0x1054` sont requises. Les listes parallèles `0x262b`, `0x2423`, `0x2425` et `0x2428 → 0x1054 → 0x1052` portent le même compteur UInt32. Elles peuvent être vides, ou ne contenir que des groupes déjà conformes à ce profil vide.
- Le nouvel ordinal est `N`, le compteur précédent. Le trailer de définition `0x262b` est `00 N 00 00 00 00 00 00 00`; le profil validé accepte les IDs `0..255`, soit au plus 256 groupes. Les noms UTF-8 sont globaux dans `0x262c` et doivent être uniques.
- Le payload `0x2628` commence par le nom, puis `00 50`, un sélecteur de largeur `0x10`, `0x20`, `0x30` ou `0x40`, `44 08 00`, l'origine opaque UInt32 LE `0xE8D4A510`, la durée little-endian de largeur sélectionnée, et deux UInt32 LE `start_samples`. Les deux champs temporels doivent tenir dans UInt32.
- Sous cette définition, `0x2523 → 0x2526` contient un payload de 65 octets. Il porte le début à `+0`, l'ID à `+16`, l'origine encodée sur cinq octets à `+29`, l'origine plus durée sur cinq octets à `+36`, et la fin à `+44`. Les autres octets sont fixes dans le profil vide observé.
- Le nouveau `0x2423` est `[group_id UInt32][name_length UInt32][nom UTF-8][cinq octets nuls]`. Le `0x2425` parallèle est un payload opaque de 103 octets du profil observé; seul son ordinal UInt32 LE à `+5` est écrit par l'API. La playlist interne `0x1052` est vide et a exactement le header `01 00 00 00 3F 00 00 00 00 01 00`.
- La macro visible est `0x1050 → 0x104f` de 35 octets, `group_id` à `+2`, `start_samples` UInt64 LE à `+7`, type `0x03` à `+15`, flag `0x01` à `+18`, et queue secondaire `00 00 01`. Trois enregistrements standards `0x0002` sont ajoutés pour le nouveau `0x2423`, `0x2425` et `0x1052`; leurs offsets synthétiques ne servent qu'à la relocalisation de `save()`.

La piste cible doit être unique. Elle peut être vide, ou contenir seulement des macros de groupes vides déjà vérifiées; les placements audio, fades et autres événements sont refusés. L'API déplace les macros par ordre chronologique après insertion. Elle retourne `group_id`, `group_name`, `track`, `start_samples`, `length_samples` et `end_samples`.

La création répétée de groupes vides sur plusieurs pistes précréées/renommées est validée dans Pro Tools. Les noms, le nombre de pistes et la répartition des groupes sont choisis par l'appelant dans les limites binaires du profil, pas déduits des conventions d'un logiciel client. Exemple de validation : annexe A (§11.2).

### 11.3 Dissolution d'un groupe simple (`delete_clip_group`)

La disposition prise en charge pour `delete_clip_group()` est volontairement étroite :

- Une unique racine `0x262c` contenant exactement un groupe simple.
- Une unique racine cachée `0x2428`, contenant un unique `0x1054`, lui-même contenant exactement une `0x1052`.
- La playlist cachée contient au moins un `0x1050`, et tous sont des placements audio (`type 0x03`, queue `00 01 01`); aucun groupe imbriqué, fondu ou événement non audio.
- La timeline principale possède exactement une macro (`queue 00 00 01`) dont l'ID égale l'ordinal du groupe.
- Une unique racine `0x2424` et une unique racine `0x2426`; leurs compteurs et ceux de `0x262c` sont égaux.
- Le `0x2423` correspondant contient la longueur du nom à `+4` et le nom UTF-8 à `+8`; l'entrée `0x2425` correspond par ordinal.

Le dégroupage :

1. Calcule `hidden_origin = min(timestamp des composants)`.
2. Rebase chaque composant avec `macro_start + hidden_timestamp - hidden_origin`, sous borne UInt64.
3. Déplace les blocs `0x1050` existants vers la playlist principale à la place de la macro, puis retrie uniquement les slots d'événements.
4. Met à jour le compteur principal avec `ancien - 1 + nombre_de_composants`.
5. Supprime la playlist cachée et met le compteur de son `0x1054` à zéro, tout en conservant les conteneurs `0x2428`/`0x1054` vides.
6. Retire le `0x262b`, le `0x2423` et le `0x2425`, puis décrémente leurs trois compteurs. Les racines vides `0x262c`, `0x2424` et `0x2426` sont conservées.
7. Enregistre tous les offsets supprimés afin que `save()` purge leurs enregistrements `0x0002`.

L'opération complète est transactionnelle. Sans racine `0x262c`, `delete_clip_group()` retourne `0`; une dissolution réussie retourne `1`. La création est exposée uniquement dans les deux profils §11.1 (audio-backed) et §11.2 (vide). Les autres liens autour de `0x2428` restent non documentés et ne doivent pas être généralisés par inférence.

## 12. Limites fonctionnelles consolidées

- Édition de sessions PTX existantes seulement; aucune création complète depuis zéro. Le seul authoring de session est le builder audio générique fondé sur un template natif strict.
- Payloads métier little-endian seulement.
- Conversions temporelles limitées à 24, 23.976 non-drop, 29.97 Drop Frame et, dans le changement non publié, 25 non-drop. Le mapping natif 25 fps et la sortie de déplacement ont été validés dans Pro Tools.
- Audio et fondus seulement sur la timeline. MIDI, contrôleurs continus, pistes/clips vidéo, Inserts, Sends, authoring I/O général, écriture/automation Pan générale, Mute automation et automation de plugins ne sont pas pris en charge. La lecture `get_track_outputs()` (§4.1.3/§4.1.6) couvre les bus mono/stéréo autonomes sur pistes Audio mono. `set_track_output()` exige des playlists nommées, réutilise un code actuel ou calibre depuis au moins deux bus distincts cohérents. Mono→Mono, Mono→Stereo avec deux pans vides vérifiés et Stereo→Mono avec pan statique centré exact et sans référence standard à la lane sont validés manuellement (§4.1.7). Stereo→Stereo préserve le pan centré statique exact et est validé manuellement (§4.1.8); réaffectation ou retrait d'un pan non centré/automatisé restent refusés. Aucune création de chemin ou de piste Audio stéréo, pan général ou autre routing multicanal n'est ajoutée. La lecture `get_track_pan()` (§10.5) reste mono; `get_track_pan_lr()` (§10.7) couvre les pans statiques mono/stéréo corroborés. L'écriture `set_track_pan()` couvre les lanes statiques existantes mono (§10.6) et stéréo (§10.8) corroborées, sans automation ni création de lane. `get_pan_automation()` (§10.9) lit les courbes mono du profil corroboré, et `set_pan_automation()` (§10.10) les remplace sur une lane existante avec prévalidation complète, la lecture stéréo §10.11 et l'écriture stéréo §10.12 sont également corroborées; un seul bus distinct affecté ne permet pas la calibration.
- Aucune création ou réorganisation de pistes, import/export audio général ou suppression arbitraire de définitions/événements. Le renommage/visibilité des slots précréés (§4.1.1) et la suppression des pistes Audio vides dans le profil strict `delete_tracks()` (§4.1.2) sont pris en charge; ce dernier n'est pas une suppression générale de pistes peuplées ou de layouts inconnus. Les autres exceptions sont le clonage WAV exact (§6.4), le peuplement audio par manifeste d'un template compatible (§6.5) et l'ajout natif monoPCM24 strict `import_audio()` (§6.9), sans conversion/copie de fichier.
- Le builder audio 6.5 reste limité aux sessions 48 kHz/23,976 et aux WAV mono WAVE_EXTENSIBLE IEEE float 32 bits munis des métadonnées BWF/UMID requises. Il n'accepte que les profils natifs `native_float_15_142` et `native_float_31_151_u32` documentés ci-dessus; il ne choisit ni ordre, ni regroupement, ni piste à partir d'une convention de filename. Ces politiques appartiennent aux applications clientes.
- Création de Clip Group limitée aux deux profils stricts documentés : le profil audio template-driven 11.1 (une région audio source existante, un prototype interne vide, un composant, une piste et une macro prototype unique) et le profil vide 11.2 (48 kHz/23.976, playlist cible vide ou ne contenant que des macros vides vérifiées, IDs `0..255`). La dissolution reste limitée au cas simple documenté.
- `create_subclip()` et les trims produisent les combinaisons vérifiées offset UInt24/longueur UInt32 (`01 30 40`) et offset UInt32/longueur UInt24 (`01 40 30`). Ils rejettent encore un sous-clip virtuel d'offset nul et de longueur supérieure à UInt24, ainsi que la combinaison offset UInt32/longueur UInt32, faute de référence Pro Tools. Le split accepte les racines courtes et longues vérifiées et une longueur droite UInt32, mais la coupe relative reste limitée à UInt24 tant que le layout du fragment gauche d'un split tardif n'a pas été observé.
- Move, duplicate, split et trims refusent les placements avec fondus attachés; la duplication ne clone pas les fades.
- Fades lus/ajoutés, sans édition de géométrie ni suppression indépendante générale. `delete_clip()` §6.6 retire le fade-out UInt24 unique vérifié avec son clip. Crossfade créé centré Equal Power seulement.
- Marqueurs ponctuels lus/ajoutés; suppression `delete_marker()` §8.2 du profil legacy à règle unique/assignment0, validée. Aucune édition de marqueur existant, création de sélection ou propriété avancée. Le filtre multi-règle en lecture ne permet pas leur suppression générale.
- Édition de Clip Gain statique seulement; extensions mixtes 9.2/9.5 préservant les enveloppes quatre/trois nœuds corroborées. Cible statique existante non partagée, clonage partagé 9.3 et premier gain mixte 9.4 validés par ouverture/sauvegarde Pro Tools sur le profil quatre points. La sortie statique avec conservation de trois points 9.5 est également validée avec sa sauvegarde native. Pas d'édition d'enveloppe, d'autre arité ou d'index UUID moderne. Volume publié : ajout/remplacement de nœuds sans suppression. Nouveau remplacement/fusion 10.2/10.4 non publié : profils mono et stéréo/mixte validés dans Pro Tools. Remplacement exigeant une enveloppe non vide; aucun writer multi-cible/automation générale.
- Résolution exacte du fichier physique lorsque le catalogue indexé `0x1004`/`0x103a` vérifié est présent; repli nominal sans garantie dans les autres layouts. Le lecteur public ne vérifie pas l'UUID BWF, tandis que le relink renouvelle et synchronise explicitement l'identité BWF/PTX de son clone.
- Arbres limités à 128 niveaux, `block_type` pris en charge sur 8 bits, contenus/offsets sur UInt16/UInt32 et fichiers sérialisés dans l'espace UInt32.
- Les révisions PTX non présentes dans le corpus peuvent contenir des flags, géométries ou conteneurs inconnus; ils sont préservés lorsqu'ils restent opaques, mais une opération qui doit les interpréter les rejette.

## Annexe A — exemples et preuves de validation

Cette annexe est **informative et historique**, pas normative. Elle conserve les observations originales qui fondent les profils précédents, y compris les conditions construites sur références natives, les valeurs exactes et les résultats manuels. Les données propres au corpus ne sont pas des contraintes d'utilisation. Une règle rédigée dans les sections 1 à 12 prime sur une formulation de statut devenue historique dans ces extraits.

Le corpus historique comprend notamment des sessions natives Pro Tools Ultimate 2024.3.1 à 23.98, 24 et 29.97 DF, ainsi que des layouts issus d'un import Premiere observés dans des sessions PTX. Ces références ne limitent pas les noms utilisables et ne prouvent pas, à elles seules, la prise en charge de toutes les structures d'une même version ou cadence.

Les compteurs de tests ci-dessous sont ceux des étapes successives; ils ne s'additionnent pas et ne représentent pas tous le total actuel. Dernier contrôle fonctionnel au 11 octobre 2026 : **588 tests avec `-W error`**, syntaxe Python 3.8 de 56 fichiers vérifiée (module/tests/checker de distribution). Le source courant contient 984 instructions `raise`, recensées en §13. Les trims en place §6.7, la conservation de plages §6.8/A.37, l'importPCM24 §6.9/A.38, la suppression de placement §6.6/A.35, la suppression de markers §8.2 et les intégrations de Pan §10.6/10.8/10.9/10.10/10.11/10.12 sont validés sur leurs profils documentés. Audit technique de la contribution clôturé dans ce périmètre; variantes sans oracle explicitement exclues/reportées. Copie portable sans fixtures :463tests exécutés/125skips. Le workflow CI est ajouté pour les futurs push/PR; ses matrices distantes ne sont pas présentées comme exécutées.

### A.1 — validation de §1.3

**Correctif local non publié — compteur de playlists** : `RAW_PREFIX_PTX_CONTENT_TYPES` définit uniquement `0x1054: 4`. Les quatre premiers octets du payload de toute track map `0x1054`, principale ou imbriquée dans une playlist de Clip Group, sont conservés comme données brutes avant toute recherche d'enfant. Ce UInt32 est le compteur de playlists : la valeur 90 (`5A 00 00 00`) ne doit jamais devenir un faux en-tête de bloc lorsqu'elle précède un `0x1052`. Aucun compteur n'est changé ou réparé automatiquement. Un préfixe tronqué est préservé par le parseur; le validateur de playlists continue à rejeter les compteurs de moins de quatre octets et les nombres incohérents. Les autres types de conteneurs ne reçoivent pas cette règle par inférence. La suite synthétique couvre 90 et 346 playlists, le nesting, les pointeurs standards et les sauvegardes. Une session native 2024.3.1 à 90 playlists Audio mono vides confirme la lecture ordonnée et deux cycles byte-identiques de sauvegarde/rechargement; elle charge aussi avant le correctif et n'est donc pas une reproduction native du bug. L'utilisateur a validé l'ouverture, les 90 pistes, la sauvegarde et la réouverture de la sortie dans Pro Tools 2024.3.1. Le fichier resauvegardé par Pro Tools charge dans l'API et reste byte-identique lors de deux nouveaux cycles API. Les deux fixtures natives font l'objet de tests optionnels locaux; elles ne sont pas distribuées avec le paquet.

### A.2 — validation de §1.4

**Étape 2 non publiée — 25 fps** : une session native Pro Tools 2024.3.1 confirme `0x204d[0] == 0x02`, à 48 kHz. L'image vaut exactement 1920 échantillons. Le clip natif de 96000 échantillons débute à `1728046080` (`10:00:00:24`) et finit à `1728142080` (`10:00:02:24`); les markers natifs sont à `1728046080`, `1728048000` (`10:00:01:00`) et `1730880000` (`10:01:00:00`). `move_clip()` vers `10:00:01:00` écrit seulement le UInt64 de placement `0x104f+7`, soit +1920 échantillons, sans toucher aux définitions, médias ou markers; la nouvelle fin est `1728144000` (`10:00:03:00`). Les tests confirment les conversions position/durée, l'arrondi à la demi-image, les transitions seconde/minute, le rejet de `FF >= 25`, les calculs à 44.1/48/96 kHz, le cycle natif byte-identique et les limites d'authoring conservées. L'utilisateur confirme l'ouverture/sauvegarde/réouverture de la sortie déplacée dans Pro Tools 2024.3.1; le fichier resauvegardé conserve le clip et les markers attendus et passe deux nouveaux cycles API byte-identiques, couverts par un second test natif optionnel. Cette extension du moteur temporel n'élargit pas les profils de création stricts 48 kHz / 23.976 du builder et des groupes vides.

**Correctif local des cadences exactes, 11 octobre 2026 — validation finale terminée** : les paires natives 24/48 et 23.976/47.952 possèdent respectivement les mêmes octets de famille 0x01/0x09, mais des UInt32 à +40 distincts (0x01/0x10 et 0x09/0x0F). Les références 59.94DF/119.88DF ont une famille 0x05, des enums exacts 0x0C/0x13 et des payloads 67/68 octets. Les valeurs de +48 changent aussi; aucune sémantique d'écriture de ce champ n'est déduite. À 48k, les marqueurs natifs de première minute légale, dix minutes et une heure valent 2882880, 28799971 et 172799827 samples aux deux cadences DF fractionnaires : premiers labels 04 au nominal60 et 08 au nominal120. Ils corroborent les comptes de quatre/huit numéros omis, indépendamment des aller-retours API.

Les onze autres cadences sélectionnées nativement corroborent les enums 0x03, 0x07, 0x08, 0x0A, 0x0B, 0x0D, 0x0E, 0x11, 0x12, 0x14 et 0x15; les variantes à nominal120 possèdent un payload de68 octets. Les positions en samples des marqueurs sont volontairement conservées dans ce lot : ce n'est pas un oracle indépendant de leur affichage TC à chaque nouvelle cadence. Avec le 25fps historique, dix-huit enums distincts disposent de fixtures locales; le 0x05 reste le mapping historique. Deux cycles sans mutation par référence sont byte-identiques. Les calculs des19 mappings sont testés avec une table rationnelle indépendante, une énumération des labels DF, bornes/refus atomiques et profils d'authoring inchangés. La suite atteint655 tests avec le corpus privé. Le mainteneur valide les trois sorties de déplacements isolés (48ND,119.88DF,120DF) dans Pro Tools. Leurs sauvegardes natives conservent respectivement527000→623000,2882880→2978880 et172627200→172723200 samples (longueur96000), les marqueurs et le WAV. Chaque sauvegarde native passe deux cycles API byte-identiques; ces résultats ne sont pas présentés comme une publication ou une validation manuelle exhaustive des19 cadences.

### A.3 — validation de §4.1

**Basic Folder, observation native en lecture seule** : dans la paire 48 kHz / enum `0x09` vérifiée, le dossier `FOLDER_TEST` possède deux miroirs `0x251a` sous `0x2519`. Leur premier octet vaut `0x0b` (les miroirs Audio valent `0x00`), la longueur du nom est le UInt32 à `+2`, et le nom commence à `+6`. Le dossier ne possède aucun `0x1052` principal : le compteur `0x1054` reste 2 et ses playlists `A_OUTSIDE` / `B_INSIDE` restent nommées, même après placement de B dans le dossier. Le lecteur retourne ces deux noms, pas le nom du Basic Folder. Les deux clips `MEDIA`, de longueur 1355354 et offset source 0, restent à 1729728000 et 1730208480 échantillons; la définition audio et le WAV sont inchangés. Deux cycles API sans mutation par session sont byte-identiques et conservent tous les champs de dossier opaques. Aucune création, modification ou reconstruction de dossier n'est implémentée; l'ouverture, la lecture, la sauvegarde et la réouverture manuelle de la copie API dans Pro Tools sont confirmées. Le fichier resauvegardé conserve les deux playlists, les deux miroirs de dossier et les clips, puis passe deux nouveaux cycles API byte-identiques. Cette observation ne valide ni les Routing Folders, ni l'imbrication, ni les playlists anonymes AAF. Le lecteur courant n'ajoute pas le fallback de catalogue `0x251a` de la contribution.

**Playlists anonymes mono, extension de lecture non publiée** : une paire native Pro Tools → AAF consolidé → Pro Tools, 48 kHz / enum `0x09`, reproduit deux playlists principales dont le header est exactement `[0 UInt32][event_count UInt32]`, soit 8 octets. Les pistes `A_OUTSIDE` / `B_INSIDE` restent décrites dans plusieurs miroirs indépendants. Le helper `_validated_anonymous_mono_track_names(N)` résout leurs noms seulement si toutes les playlists principales sont anonymes et que le profil ci-dessous concorde. L'égalité de nombres seule ne suffit pas; aucun fallback mono/stéréo du contributeur n'est intégré.

La référence possède quatre définitions de clip et deux médias physiques après réimport (une définition/un média avant). Ses placements conservent longueurs 1355354, offsets source 0, débuts 1729728000 / 1730208480, fins 1731083354 / 1731563834; noms `MEDIA-01` / `MEDIA_01-01` et fichiers `aafzhhsBMCSpvcmRYPY.wav` / `aafyvqhCQCSpvcmRYPY.wav`. Les WAV consolidés sont byte-identiques entre export et réimport; leur média contient un sample supplémentaire déjà présent à l'export, sans changement de longueur de placement. Les deux PTX passent deux cycles API byte-identiques. L'ouverture et la sauvegarde de la copie API dans Pro Tools sont confirmées. Le PTX resauvegardé garde ses deux headers anonymes, ses quatre définitions, deux médias et sa géométrie exacte; il passe deux nouveaux cycles API byte-identiques, couverts par une quatrième régression native optionnelle. Les WAV restent inchangés. Lecture audio et réouverture manuelles également confirmées; aucune compatibilité générale avec toutes les sessions AAF, stéréo ou folders anonymes n'est revendiquée.

### A.4 — validation de §4.1.1

`set_visible_tracks()` exige une liste non vide, sans doublon, composée uniquement de noms de pistes existants. Il ne modifie ni le nombre de playlists, ni les offsets, ni le catalogue média, et restaure transactionnellement l'arbre en cas de profil ambigu ou incomplet. Les tests manuels ont validé l'affichage des pistes 1, 2, 3 et 9 dans un pool natif de dix pistes.

### A.5 — validation de §4.1.3

**Preuve native** : dans la paire 48 kHz / enum `0x09`, les 90 entrées live `0x2602` sont inchangées. Before : `A_OUTSIDE → API_ROUTE_A`, `B_INSIDE → API_ROUTE_A`; after : seule `A_OUTSIDE → API_ROUTE_B_LONG`. Chaque piste a un seul descripteur. Pour A, code `65 → 66`, identité `376de8e605e7106c → 376de8e650f2106c`, longueur du nom `11 → 16`, taille de record `70 → 75`; le trailer reste identique. Le record de B est byte-identique. Ces codes ne deviennent pas des constantes ou une formule de writer.

Les WAV et la géométrie des clips restent exacts. La sauvegarde native a aussi changé des états UI, des identités opaques et des références de dossier : ne pas assimiler tous ces octets au routing ni déclarer, depuis cette première paire seule, qu'un simple remplacement de `0x260e` suffit à écrire un fichier valide. Quatorze régressions (dont quatre natives optionnelles) couvrent la lecture actuelle, les refus sans mutation et deux cycles API byte-identiques par PTX. L'utilisateur confirme l'ouverture, les sorties et clips attendus, la sauvegarde et la réouverture de la copie API dans Pro Tools. Le fichier resauvegardé conserve les descripteurs principaux, les 90 chemins live et la géométrie exacte des clips; deux nouveaux cycles API restent byte-identiques. Le profil de lecture est validé. L'écriture limitée de §4.1.4 est étudiée séparément; aucun support de sortie multiple, stéréo, send ou hardware n'est ajouté.

### A.6 — validation de §4.1.4

**Nouvelle paire native** : before contient `A_OUTSIDE → API_ROUTE_B_LONG`, `B_INSIDE → API_ROUTE_A`; after change seulement l'affectation visible d'A vers A. Le nouveau payload d'A est byte-identique au descripteur déjà présent sur B : taille `75 → 70`, code `66 → 65`, identité `376de8e650f2106c → 376de8e605e7106c`, longueur du nom `16 → 11`. Le descripteur de B et les catalogues complets `0x2603`/`0x1022` sont inchangés. Clips, positions, longueurs et WAV sont identiques. Le save natif modifie aussi des IDs opaques dans les suffixes des deux `0x261b` (pas seulement la piste routée), de l'UI, des références de dossier et des identités de médias/clips; ces données ne sont pas normalisées par le writer.

Treize tests, dont quatre natifs optionnels, couvrent paramètres/UTF-8, cas identique, prévalidation tardive sans mutation, codes contradictoires, bloc inconnu, refus de bus absents ou non calibrables, refus anonyme, appels composés depuis l'arbre courant et réutilisation sans inférence. La sortie API réduit le nom comme la référence native et ne change que le payload ciblé en mémoire. Des tests de save/reload vérifient aussi l'augmentation de taille vers le bus long déjà employé, puis deux cycles byte-identiques par sortie. **L'utilisateur confirme l'ouverture, les sorties et clips attendus, la sauvegarde et la réouverture de la sortie API à nom raccourci dans Pro Tools.** Le fichier resauvegardé conserve les deux descripteurs, les catalogues `0x2603`/`0x1022` et la géométrie exacte des clips; deux nouveaux cycles API sont byte-identiques. Le refus d'un bus inutilisé avec un seul bus distinct affecté et le cas identique restent vérifiés sur cette sauvegarde. La suite atteignait 287 tests avant §4.1.5. Cette preuve ne valide pas à elle seule une génération de code pour un bus inutilisé, ni une écriture stéréo/multi-sorties/matériel/sends, ni les profils cachés/réordonnés. La croissance du nom est couverte automatiquement, sans ouverture manuelle distincte revendiquée à ce stade.

### A.7 — validation de §4.1.5

**Corroboration native distante** : les catalogues complets `0x2603`/`0x1022` sont inchangés dans la nouvelle paire; seule A_OUTSIDE passe du bus long à ADR, B_INSIDE reste sur A. Le descripteur d'ADR écrit par Pro Tools est mono de type `0x09`, longueur 62, même préfixe/trailer que les références. Il contient l'identité `bb0655e436817669`, le nom de 3 octets et le code `0x19`.

| Chemin natif | Ordinal live zéro-based | Code de descripteur | Code − ordinal |
|---|---:|---:|---:|
| ADR | 12 | 25 (`0x19`) | 13 |
| API_ROUTE_A | 88 | 101 (`0x65`) | 13 |
| API_ROUTE_B_LONG | 89 | 102 (`0x66`) | 13 |

La valeur 13 est une observation de cette session, **pas une constante du writer**. Les deux références avant mutation corroborent la base et le nouvel after la confirme sur une entrée éloignée. Les clips, placements et WAV sont identiques. Les changements natifs d'UI, références de dossier et identités opaques ne sont pas copiés ou normalisés.

Douze régressions, dont cinq natives optionnelles : compte d'entrées opaques intermédiaires, UTF-8, appels composés, perte du second bus distinct, bases incohérentes/négatives, borne UInt8 255 et overflow sans wrap, target mono/autonome/unique, types de catalogue inconnus et accord de tous les anchors. Le payload API pour ADR correspond exactement au after natif; seul ce payload change dans l'arbre. Save/reload et deux cycles byte-identiques sont vérifiés. Le retour vers le bus long désormais non affecté se calibre depuis ADR/A et rétablit **tout le PTX before byte-identique**. **Validation utilisateur reçue pour les deux sorties : ouverture, sorties/clips attendus, sauvegarde et réouverture dans Pro Tools.** Les deux fichiers resauvegardés conservent descripteurs, catalogues I/O et géométrie de timeline; chacun passe deux nouveaux cycles API byte-identiques et un aller-retour calibré sans effet final sur ses octets. Deux régressions supplémentaires couvrent ces sauvegardes et portent la suite à 299 tests. Cela ne valide pas un moteur général d'I/O, les bus stéréo/multicanaux, hardware, Sends, catalogues factory, maps anonymes ou codes arbitraires.

### A.8 — validation de §4.1.6

Une affectation strictement identique retourne `0` sans pan modifié. Le retour vers un bus mono possède le profil natif strict de §4.1.7; le changement vers un autre bus stéréo possède la nouvelle branche limitée de §4.1.8, encore en attente de validation manuelle. Ce writer ne fournit pas d'API de pan ni de pistes Audio stéréo; Mono→Stereo reste limité aux conteneurs initialement vides, Stereo→Mono et Stereo→Stereo au seul pan statique centré vérifié.

**Preuve native :** deux pistes mono, 48 kHz / enum `0x09`, mêmes clips/WAV/catalogues avant/après. Pour la piste cible, bus long mono → bus stéréo entier : code `0x66 → 0x2b`, nom UTF-8 `16 → 7` octets, payload `75 → 66`, identité du chemin stéréo à T+20, trailer ci-dessus et activation du pan centré ci-dessus. L'ordinal 30 et code 43 donnent la même base 13 que les deux anchors mono, sans constante dans le code. La sortie de l'autre piste reste byte-identique. Les changements natifs de dossiers, UI, identités opaques et timestamps de sauvegarde ne sont pas transposés à l'API.

Douze tests ajoutés, dont quatre natifs optionnels : égalité exacte des nouveaux route/pan et absence d'autres mutations, UTF-8, code réel d'une destination déjà utilisée sans clonage de pan, calibration depuis les affectations mono/stéréo courantes, géométries/noms/identités/aliases/largeurs invalides, profils pan inconnus, refus des transitions non vérifiées, pointeurs natifs et deux cycles API byte-identiques sur originaux et sortie modifiée. **Validation utilisateur reçue : ouverture/sauvegarde/réouverture conforme dans Pro Tools.** La sauvegarde native conserve les route/pan exacts, les catalogues I/O et la géométrie des placements, puis passe deux nouveaux cycles API byte-identiques. Une régression supplémentaire couvre cette sauvegarde et l'appel identique sans effet. La suite atteignait 311 tests avant le retour de §4.1.7; son ancien test de refus général Stereo→Mono vérifie maintenant le refus du pan non centré. Aucun retrait du pan n'est implémenté par supposition.

Observation complémentaire de catalogue : un deuxième bus autonome créé nativement possède également le préfixe `02 01` et une queue de 44 octets strictement conforme à §4.1.6, avec ports 139/140 et identité distincte non nulle. Les sorties et pans des pistes restent inchangés lors de cette préparation; le before préparé passe deux cycles API byte-identiques. La préparation seule ne prouve pas une transition Stereo→Stereo; sa paire ultérieure et les limites d'écriture sont décrites en §4.1.8.

### A.9 — validation de §4.1.7

La nouvelle paire native montre uniquement, dans l'état fonctionnel de la piste ciblée :

- `0x260e` : code `0x2b → 0x66`, identité et nom du bus mono long, longueur UTF-8 `7 → 16`, payload `66 → 75`, trailer mono de 19 octets rétabli;
- premier `0x260c` : suppression de la lane `0x260a` centrée et remplacement des trois items `[01][lane][00×13]` par un seul segment `00×14`;
- deuxième `0x260c` : vide et byte-identique;
- aucun changement de la route de l'autre piste, catalogues I/O, clips, placements ou WAV.

Si la lane possède un `original_offset` existant non nul, il faut une unique racine `0x0002`. `_raw_0002_payload()` et `_validate_0002_record_layout()` valident son payload actuel; ce profil exige également des records standards non vides (un payload sans records ne prouve pas l'absence de références). Puis leurs pointeurs UInt32 LE sont examinés : **toute référence à cet offset est refusée**, pas purgée expérimentalement. La paire native conserve ses 172 records standards avec mêmes métadonnées/cibles logiques; aucun ne référence la lane retirée. Une lane créée en mémoire à offset 0 n'a pas de référence source à examiner.

Dix tests supplémentaires, dont six natifs optionnels : destination mono déjà utilisée (code réel non calibré) ou inutilisée/calibrée, compositions depuis l'arbre courant, refus pan/automation/table/référence sans modification du route ni des métadonnées, exactitude route/pan native et absence d'autres mutations, conservation de l'offset parent, bookkeeping de la lane retirée, cycles API sur les deux originaux et sortie modifiée, restauration du fichier entier, sauvegarde et réouverture Pro Tools. **Ouverture/sauvegarde/réouverture manuelles confirmées** : les références natives après sauvegarde et après réouverture gardent les route/pans vides, catalogues et placements exacts; chacune passe deux cycles API byte-identiques et une composition mono/stéréo/mono byte-identique. **Suite locale 321 tests réussis; étape clôturée.** Les autres changements natifs d'UI, chemins, timestamps de sauvegarde et identités de clips restent opaques, pas transposés au writer.

### A.10 — validation de §4.1.8

**Statut : comparaison native, tests API et ouverture/sauvegarde/réouverture de la sortie API dans Pro Tools validés; étape clôturée, support non publié.** La piste Audio reste mono; il s'agit uniquement d'un changement entre deux bus autonomes stéréo de géométrie §4.1.6. Le premier after reçu changeait une autre piste de mono vers stéréo : conservé, mais exclu de l'oracle de cette nouvelle transition.

La paire corrigée conserve les deux pans, la sortie de l'autre piste, les catalogues complets `0x2603`/`0x1022`, les clips/placements/WAV et les 172 records standards de `0x0002` avec leurs métadonnées/cibles logiques. Pour la seule sortie cible `0x260e`, code `0x2b → 0x67`, identité `032a39e510cc6364 → ff2ee9e6deff3bee`, longueur du nom UTF-8 `7 → 21`, payload `66 → 80` octets. Le trailer stéréo de 19 octets reste exactement `0101ffffffffffffffff00ffffffff000c0100`. Le nouveau bus est à l'ordinal 90, code 103 : même base 13 que les anchors existants, sans constante de base dans le writer. Ses ports 139/140 ne servent pas à calculer le code.

Onze régressions, dont quatre natives optionnelles : target inutilisé UTF-8/code calibré, code réel arbitraire réutilisé, pan donneur non cloné, objets/offsets/bookkeeping préservés, compositions avec changement de largeur, refus pan/target/alias/calibration tardifs sans mutation, manque de second bus distinct et overflow UInt8 sans wrap, no-op, exactitude native du seul descripteur remplacé, conservation des records standards et deux cycles API byte-identiques sur les originaux/sortie modifiée. Le retour vers le premier bus stéréo reproduit **tout le before byte-identique** après croissance/réduction du nom. **Ouverture/sauvegarde/réouverture manuelles confirmées** : le fichier resauvegardé conserve routes, pan centré, catalogues, placements et cibles de pointeurs, puis passe deux nouveaux cycles API byte-identiques et une composition entre les deux bus reproduisant tout le fichier. **332 tests réussis avec `-W error`, syntaxe Python 3.8 vérifiée; étape clôturée.** Les autres différences natives de sauvegarde restent opaques et ne sont pas copiées au writer. Aucun support de pan arbitraire, pistes Audio stéréo, routing matériel/multicanal/sends ou création de bus n'est revendiqué.

### A.11 — validation de §5.1

La référence native de trim du début à 48 kHz / enum `0x09` confirme précisément `01 10 30 44 08` à partir de `A` : offset UInt8 de 100 à `A+5`, longueur UInt24 de 8680572 à `A+6`, deux UInt32 égaux à 1729728100 à `A+9` et `A+13`. Le placement passe de 1729728000 à 1729728100, et sa fin reste 1738408672. Le parent conserve ses attributs et le WAV reste byte-identique. Ce marqueur `0x44` et les autres champs sont conservés, pas normalisés vers un marqueur supposé `0x14`. L'ouverture, la lecture, la sauvegarde et la réouverture de la copie API dans Pro Tools sont confirmées. Le fichier resauvegardé conserve exactement offset, durée et placement, puis passe deux cycles API sans mutation byte-identiques; la régression native couvre aussi cet état après Pro Tools. `get_relink_write_status()` retourne `supported=False`, code `unsupported_clip_layout`; `relink_clip()` refuse ce profil sans mutation ni clone WAV. Aucun nouveau writer UInt8 ni extension des tables de relink n'est intégré.

### A.12 — validation de §5.3

Le corpus comparatif minimal possède `K=1` et les relations `N+3`/`N+2`/`N+1`. Le diagnostic Pro Tools a confirmé qu'ajouter un fichier sans incrémenter ce troisième compteur laisse la piste visible dans la fenêtre d'import, mais bloque Pro Tools au chargement du catalogue média. Une correction isolée de ce compteur, puis une sortie complète avec WAV généré, se sont ouvertes correctement.

Un corpus de production possède `K=3`, ce qui confirme la règle générale à plusieurs niveaux. Les libellés et compteurs de cette queue sont des métadonnées PTX opaques, pas des dossiers du poste courant. Le dossier physique conventionnel reste le dossier frère `Audio Files` de la session; `relink_clip()` reçoit toutefois les chemins source et destination explicitement et ne les déduit pas de ces libellés internes.

### A.13 — validation de §6.4

**Limite et contournement observés (2026-07-21).** Un `Save Copy In…` Pro Tools d'un corpus Premiere conserve le header `0x2106` de 173 octets, son texte Adobe Premiere et la géométrie virtuelle `0x3001/0x30/0x04/0x08`; il ne convertit pas le clip en média natif. `Consolidate Clip` crée en revanche un header 151 octets et un clip parent natif. Une sortie relinkée de cette session consolidée s'est ouverte correctement dans Pro Tools. Le relink écrit du layout hérité reste non spécifié; la consolidation préalable est le contournement validé.

### A.14 — validation de §6.5

Le builder ne génère ni `DGDA`, ni `minf`, ni `regn`. Les sources doivent déjà fournir le BWF/UMID requis; les comparaisons natives disponibles montrent que Pro Tools préserve les chunks et échantillons d'origine avant d'ajouter ces trois chunks. La validation automatisée garantit que les WAV copiés sont byte-for-byte identiques aux sources. La sortie corrigée à deux médias a été ouverte, lue, sauvegardée et rouverte avec succès dans Pro Tools. Le cycle natif a conservé deux entrées physiques distinctes, leurs longueurs, les records fixes 48/104 octets, les IDs/index `0`/`1`, les timestamps, l'overlap d'un échantillon et les hashes des WAV.

### A.15 — validation de §7.1

Deux sessions natives Pro Tools 2024.3.1 confirment ce profil à 48 kHz. À 25 fps, le clip audio s'étend de `1728000000` à `1728192192` (192192 échantillons), le fade-out de `1728096000` à `1728192192` (96192 échantillons). À 23.976, le clip s'étend de `1729728000` à `1729920192` (même longueur), le fade-out de `1729824096` à `1729920192` (96096 échantillons). Les TC arrondis des fades sont, dans les deux sessions, `10:00:02:00 → 10:00:04:00`. Le lecteur retourne les valeurs exactes du PTX, pas une durée supposée de 96000 échantillons depuis le label « 2 secondes ». Les comparaisons avant/après à 25 fps confirment l'absence de changement de la définition et du placement audio. Les copies API sans mutation sont byte-identiques; leur ouverture, sauvegarde et réouverture dans Pro Tools ont été confirmées aux deux cadences. Les fichiers sauvegardés par Pro Tools conservent les positions et durées exactes; deux cycles API sans mutation sont ensuite byte-identiques pour chaque fichier. Les quatre cas natifs (avant et après sauvegarde Pro Tools, aux deux cadences) sont couverts par les tests optionnels locaux.

### A.16 — validation de §9.2

La paire native statique + enveloppe vérifie un dictionnaire brut de 148 octets : count UInt32 = 4, trois records statiques de 30 octets et un record d'enveloppe de 54 octets. Pro Tools réordonne ces quatre entrées entre before et after et réassigne les index des définitions; les enregistrements associés aux mêmes noms de clips sont inchangés, excepté MEDIA-01 passant +3→+6 dB. L'API conserve l'ordre et les index existants au lieu de copier cette réorganisation.

Preuve native : courbe B aux positions relatives `0, 96096, 384384, 576576`, soit départ B puis TC absolus 10:00:12:00/18:00/22:00 dans cette session 48 kHz/23.976. Les valeurs natives sont `0, 0, environ -5.952, environ -0.000006 dB`; l'API ne les arrondit pas vers les valeurs nominales demandées. Le record entier est byte-identique avant/après, même lorsque Pro Tools change son ordinal.

Les seize premières régressions ont porté la suite à 402 tests. La sortie API ne différait du before que d'un octet chiffré et conservait 172 records/cibles logiques et tous les médias/placements. **Ouverture/lecture/sauvegarde/réouverture dans Pro Tools confirmées le 7 octobre 2026**. La sauvegarde native conserve les quatre gains par nom (dont +6 dB cible et courbe exacte), Clip List/placements/pans/routes/Volume et les mêmes cibles de pointeurs. Ses métadonnées opaques/path de médias peuvent évoluer nativement; elles ne sont pas normalisées par l'API. Deux cycles API et compositions +6 no-op/+3/+6 restituent le fichier entier byte-identique. Quatrième fixture/régression native locale ajoutée. Profils UUID modernes et édition d'enveloppe hors portée.

### A.17 — validation de §9.3

Le partage de validation est **construit de façon contrôlée sur la référence native resauvegardée de 9.2**, pas observé dans une nouvelle paire native : l'entrée parent MEDIA de Clip List (non placée) est reliée au point +6 de MEDIA-01. Ce changement d'index connu ne change qu'un octet de la base. Le writer change alors seulement MEDIA-01 vers +3 par clonage; MEDIA reste à +6 et la courbe de MEDIA_01-01 reste active et exacte. Count 4→5, payload 148→178; index cible 4, ancien index partagé 2. Les 172 records/cibles logiques, pistes, placements et WAV sont conservés.

**Ouverture/lecture/sauvegarde/réouverture Pro Tools confirmées**. La sauvegarde native compacte le dictionnaire à 4 records/148 octets, enlève l'ancien record inutilisé et relie la cible à l'index 3; MEDIA reste +6/index 2 et la courbe reste byte-identique/index 0. Ce compactage n'est pas une règle d'écriture API : contrôler les gains par nom de définition, pas par ancien ordinal. Clip List/placements/172 records-cibles/Volume/pans/routes/WAV conservés; deux cycles et modifications indépendantes +5/-10 puis +6/+3 restituent le fichier entier. Fixture/régression de save natif ajoutée. Les premiers 21 tests mixtes portaient la suite à 407 tests; la suite courante et l'ajout de premier gain sont décrits en 9.4. Autres enveloppes, édition de courbe et profils UUID modernes restent hors portée.

### A.18 — validation de §9.4

Test **construit**, pas nouvelle paire native : base resauvegardée validée de 9.3; seul l'index de MEDIA-01 passe de 3 à -1 (quatre octets changés), dictionnaire inchangé. L'API attribue -3 dB : nouvel index 4, count 4→5, payload 148→178; témoin MEDIA +6 et courbe de B préservés. Ancien record +3 devenu inutilisé conservé, pas normalisé. Deux cycles exacts, mises à jour +6/-10/-3 sans augmentation de count et 172 records/cibles logiques inchangés. **Ouverture et sauvegarde dans Pro Tools confirmées le 8 octobre 2026**. La sauvegarde native compacte à count 4/148, records 30/30/30/54, cible -3/index 2, témoin +6/index 0, courbe B exacte/index 3 et autre parent 0/index 1. Clip List/pistes/placements/172 cibles/Volume/Pan/routes/médias conservés. Deux nouveaux cycles API exacts et compositions indépendantes restaurent le fichier entier sans ajout de point. La réorganisation native n'est pas transposée en normalisation du writer.

Suite actuelle : **413 tests avec -W error**, AST Python 3.8 valide. 27 tests mixtes : 19 synthétiques, 6 natifs optionnels, 2 sur bases natives à conditions construites (partage/absence). Nouveaux contrôles de la sauvegarde compactée de 9.3/9.4, ajout initial, plusieurs -1 indépendants, sentinelle -inf et réutilisation du point, refus tardifs de record/enveloppe/index sans ajout partiel, cycles/positions/pointeurs et compositions exacts. Les 844 raises et contrats d'erreur restent inchangés.

### A.19 — validation de §9.5

Une seconde paire native supprime exactement le point de B à 96096 samples relatifs, en gardant ses trois autres positions/valeurs byte-identiques. Le dictionnaire passe de 148 à 140 octets, toujours count 4 : trois statiques de 30 octets et une enveloppe de **46 octets**, taille déclarée **0x26**. Pro Tools permute les index des statiques -3/0; les liens sont comparés par nom de définition, jamais en supposant un ordre immuable.

Header exact de 22 octets : `01 46 01 00 26 00 00 00 00 00 03 00 00 00 04 00 02 00 00 00 00 00`. Padding +8 nul, count UInt32 +10 = 3, flags UInt16 +14 = 4, segments UInt32 +16 = 2, réservé UInt16 +20 nul. Trois paires `[position UInt32, valeur Float32]` à +22/+30/+38; pas de terminateur. Positions natives **0, 384384, 576576** (départ B / 10:00:18:00 / 10:00:22:00), valeurs exactes reprises du record quatre nœuds, pas arrondies. Les règles premier sample zéro/croissance stricte/valeurs finies de 9.2 s'appliquent aux trois nœuds.

Le validateur sélectionne uniquement les deux profils d'enveloppe explicitement corroborés : **0x26→3** et **0x2e→4**, avec leurs headers complets. Il n'infère pas le nombre de nœuds d'une taille inconnue et n'accepte pas un autre header de même longueur. La marche bornée par `8 + size`, le compteur exact et l'épuisement sans octets restants sont inchangés. Le refus ancien de 0x26 a été reproduit sans mutation avant cette extension. Les tailles deux/cinq nœuds restent refusées; aucune recherche globale de magic ni queue UUID heuristique.

Le writer statique ne change pas : un statique non partagé est réécrit à son offset +26, un partage est cloné selon 9.3 et un index -1 reçoit son point selon 9.4. Les tests synthétiques couvrent le mélange des courbes trois/quatre points, ajout/partage, interleaving et atomicité; **une cible d'enveloppe reste toujours refusée**, aucune création/édition de courbe. La seule nouvelle sortie native-derived change A_OUTSIDE/MEDIA-01 de -3 à +3 et conserve B intégralement : un octet chiffré modifié, count 4/payload 140/order/index inchangés. Six cycles API sur paire/sortie exacts, compositions exactes et retour -3 restituant le fichier native after entier; clips/pistes/timeline/172 cibles/Volume/Pan/routes/WAV conservés. **Validation et sauvegarde Pro Tools confirmées le 8 octobre 2026** : dictionnaire toujours count 4/140, records réordonnés 30/46/30/30, cible +3 et courbe trois nœuds exactes par nom de définition; clips/pistes/timeline/172 cibles/Volume/Pan/routes/identités média/WAV conservés. Deux nouveaux cycles API sont byte-identiques; les mises à jour -10/-3/+3 restituent le fichier natif entier, sans effacer ses nouvelles métadonnées pour égaler la référence précédente. Étape clôturée; aucune normalisation native transposée au writer.

Suite courante : **419 tests avec -W error**, AST Python 3.8 valide, **844 raises inchangés**. 33 tests mixtes : 21 synthétiques, 10 natifs optionnels, 2 sur bases natives à conditions construites. Paire réelle trois points et sauvegarde native de la sortie modifiée en fixtures locales ignorées. Pas de nouveau paramètre public, de changement de version/licence ou d'élargissement aux autres enveloppes/UUID.

### A.20 — validation de §10.1

**Preuve native** : à 48 kHz/enum `0x09`, before : `(0, 0)`; after pour la piste ciblée : `(0, 0)` puis `(1730208480, 60)`, soit `10:00:10:00 / +6 dB`. Payload `30 → 36`, taille `20 → 26`, compteur `1 → 2`, segments `0 → 1`; autres lanes/pans/routes/catalogues/clips/WAV et 172 records/cibles logiques inchangés. Deux octets de `0x1029` changent nativement à `+1` et `+87` de `0` à `60`. Leur rôle n'est pas établi : aucun cache/fader/mirror n'est normalisé ou copié par supposition. Douze régressions dont trois natives optionnelles vérifient lecture exacte, refus sans mutation et deux cycles de sauvegarde/relecture byte-identiques par original; le writer historique reproduit le seul payload de Volume sans copier `0x1029`, sans certification du writer bulk. **344 tests réussis avec `-W error`, syntaxe Python 3.8 vérifiée; lecture validée sur cette paire native.** Aucune nouvelle écriture n'est intégrée dans cette étape.

### A.21 — validation de §10.2

Preuves : 15 régressions (11 synthétiques/4 natives optionnelles); le remplacement par `(0, 0 dB), (1730208480, +6 dB)` et la fusion du seul second point reproduisent exactement le payload natif et donnent des PTX bit-identiques. Deux cycles de sauvegarde/relecture exacts et retour à `(0, 0 dB)` restaurant intégralement le before. Refus tardifs/types/limites/générateur interrompu sans mutation vérifiés. **359 tests avec -W error**, AST Python 3.8 valide. **Ouverture/sauvegarde/réouverture du PTX écrit confirmées dans Pro Tools le 6 octobre 2026**. La sauvegarde native conserve ces points, les autres lanes/pans/routes/clips et les 172 records/cibles logiques; deux cycles API et compositions remplacement/fusion restituent le fichier resauvegardé bit pour bit. Les différences natives opaques `0x1029` à +1/+87 ne sont pas copiées : Pro Tools les modifie comme dans l'after natif, sans établir leur rôle; aucune hypothèse de cache/fader ni synchronisation ajoutée. Étape mono clôturée, non publiée; pistes stéréo/multi-lanes et autres profils hors portée. `add_volume_node()` inchangé.

### A.22 — validation de §10.3

La paire native mono–stéréo–mono confirme **trois pistes/slots `0x261c`, quatre playlists principales `0x1052`, trois enveloppes de Volume**. La stéréo utilise deux playlists de même nom (canaux L/R), mais une seule lane Volume dans son état principal. Il est incorrect de nommer la troisième lane selon la troisième playlist : cette dernière est encore le canal droit de la stéréo. Le résultat public conserve le format 10.1 et l'ordinal de slot (0,1,2), sans entrée supplémentaire par canal.

Preuves natives : le Volume de la piste stéréo seul passe de 30 à 36 octets, `(0,0)` puis `(1730208480,60)` à 48 kHz/enum `0x09`. Les monos, pans L/R, routage, autres lanes, Clip List, placements et WAV sont inchangés; les **179 records/cibles logiques** restent les mêmes. Opaque `0x1029` : seules positions +1/+87 changent de 0 à 60 sur la stéréo, sans interprétation/synchronisation API. Treize tests (10 synthétiques/3 natifs optionnels), dont plusieurs positions/stéréos adjacentes synthétiques; deux cycles API par original byte-identiques. **372 tests avec -W error**, AST Python 3.8 valide. Lecture clôturée sur la paire native produite dans Pro Tools; aucun nouvel authored PTX à valider pour ce lecteur.

**Séparation lecture/écriture** : lors de l'étape 17, `set_volume_automation()` restait mono strict. L'extension d'écriture stéréo/mixte est décrite en 10.4 avec son statut de validation distinct. `add_volume_node()`, `get_tracks()`, lecteurs de timeline, resolver anonyme et routing ne changent pas de contrat. Aucun lecteur Pan/gain, piste non Audio, largeur >2, profil caché/réordonné/anonyme ou multi-lane alternatif revendiqué.

### A.23 — validation de §10.4

Quatorze régressions (10 synthétiques/4 natives optionnelles) : payload cible exactement égal au Volume du after natif; remplacement par `(0,0 dB),(1730208480,+6 dB)` et fusion du second point produisent des PTX bit-identiques. Deux cycles API exacts, retrait du point restaurant tout le before, compositions successives mono–stéréo–mono et cible mono après stéréo vérifiés. Tests de limites/types/arrondi/no-op, pans L/R et autres lanes conservés, input tardif/générateur interrompu, identités/width/ordinal/state/enveloppe tardive invalides sans mutation. Deux anciens tests de refus d'écriture stéréo portent désormais sur une largeur corrompue. **386 tests avec -W error**, AST Python 3.8 valide; 836 raises inchangés.

**Validation Pro Tools et contrôle de la sauvegarde native reçus le 6 octobre 2026.** Points exacts, pans L/R, routes, autres lanes, Clip List/placements, WAV et 179 records/cibles logiques conservés. Pro Tools modifie les deux octets opaques `0x1029` comme dans l'after natif; aucun rôle de cache/fader ni synchronisation ajoutée. Deux cycles API et compositions remplacement/fusion, y compris modifications puis restauration des monos, restituent ce fichier resauvegardé bit pour bit. Étape 18 clôturée, extension non publiée; la validation mono de 10.2 demeure acquise. Aucun writer multi-cible/automation générale ni extension des autres API revendiqué.

### A.24 — validation de §10.5

Paire native centre→L50 : seuls +26/+27 de la lane ciblée passent à `ce ff`
(-50). Deux champs corrélés du `0x1029` A_OUTSIDE de **279 octets** passent
aussi 0→-50 en **Int32 LE à +13/+99**; les autres octets et autres états UI
restent exacts. Ce sont des observations de cette paire, pas une preuve
d'un rôle général cache/fader ni un profil d'écriture ajouté. Le lecteur ne
lit ni ne modifie ces miroirs. Les changements de métadonnées natives et
chemins externes ne sont pas normalisés. Pans C_STEREO/B_INSIDE, Volume,
Clip Gain, définitions `0x2628`, pistes/clips/timeline, catalogue `0x2602`,
routes `0x260e`, descriptor `0x1015`, médias `0x1001`, WAV et **179 records/cibles
logiques de pointeurs** sont conservés. Quatre cycles API sur les deux
originaux sont byte-identiques; aucune sortie API modifiée à faire ouvrir
pour cette étape strictement en lecture seule.

Dix régressions (huit synthétiques/deux natives locales optionnelles) :
valeurs/signes/bornes, absence versus centre, noms invalides, cible mono après
stéréo, cache inutilisable/arbre modifié, géométrie/header/sample/terminateur,
rejet d'automation et d'une autre mono malformée sans mutation, gardes
identity/state/Volume, exactitude native et cycles. **429 tests avec -W error**,
41 fichiers Python 3.8 valides, **850 raises**. Étape de lecture clôturée;
écriture mono/stéréo et automation Pan restent à auditer séparément.

### A.25 — validation de §11.1

La validation manuelle Pro Tools a confirmé des sorties API de durée fixe et variable, y compris sur des pistes distinctes.

### A.26 — validation de §11.2

La validation manuelle Pro Tools du 12 août 2026 a confirmé un groupe vide de 1 seconde, puis une session de pool normalisée dont les dix pistes ont été renommées `1` à `10` et qui contient 55 groupes vides : la piste `N` contient `N` groupes, chacun nommé avec son TC In et TC Out. Cette validation couvre l'enchaînement `rename_track()` puis `create_empty_clip_group()` à répétition sur plusieurs pistes.

### A.27 — validation de §10.6, depuis une expérience isolée

Un candidat d'audit réutilise la référence native centrée et remplace
uniquement l'Int16 +26 de sa lane Pan principale par -50. Le payload ciblé
est identique à celui de l'oracle L50. Aucun autre octet déchiffré ne change,
notamment les deux champs UI corrélés précédemment observés. Ce choix est
une expérience contrôlée pour établir si la lane suffit à Pro Tools,
**pas une règle générale d'écriture ni une méthode publique prise en charge**.

Candidat de 179238 octets, SHA-256
`65e8ae822fda728d5b9ea5faafdb10740b41a5a1cd66585008410ad104512b59`;
deux octets chiffrés modifiés, offsets absolus 157030/157031 propres à cette
référence. Deux cycles API exacts et retour à centre restituant le before
entier; 179 cibles logiques, autres pans, états UI, Volume, Clip Gain,
clips/placements, routing, catalogue média et WAV conservés. Suite lors de l'essai : 429
tests avec -W error. Critères de validation demandés initialement : L50 dès ouverture
et pendant lecture, conservation après sauvegarde/fermeture/réouverture.
Ne pas intégrer/publier un writer avant ce retour ni reporter ces offsets
absolus ou champs UI sur d'autres sessions par heuristique.

**Validation utilisateur reçue le 10 octobre 2026 : « all good » après
ouverture/lecture/sauvegarde/réouverture.** Fichier resauvegardé de 179229
octets, SHA-256 `fa1f83961bf68ba263eb4a146282456effa785afd12b1994dd6baf0048313086`.
Lane Pan toujours -50; témoins stéréo -100/+100 et mono sans lane, Volume,
Clip Gain, clips/placements, routing, médias/catalogue et 179 cibles logiques
conservés. La sauvegarde native a écrit -50 dans les deux champs UI corrélés;
ce comportement n'est pas transposé au writer. Deux nouveaux cycles API
exacts et compositions -100/0/+50/+100/-50 restituent le fichier natif entier.
`set_track_pan()` intégré après ce retour, sans normaliser les UI.
Dix nouveaux tests (sept synthétiques, deux natifs sur paire et un sur cette
sauvegarde) : 439 tests avec -W error, syntaxe Python 3.8 de 42 fichiers,
854 raises. Étape mono clôturée; stéréo et automation restent distinctes.

### A.28 — validation de §10.7

Paire native Pro Tools 2024.3.1, 48 kHz/enum 0x09, ordre des slots
mono–stéréo–mono. Dans le slot `C_STEREO`, seul Int16 +26 de la lane du
conteneur d'index 8 passe -100→-50; le conteneur d'index 9 reste à +100.
Header/30 octets/terminateur inchangés. Les deux champs UI Int32 corrélés
du `0x1029` cible de 279 octets, à +13/+99, passent aussi -100→-50; autres
octets UI et autres états UI exacts. Le lecteur ne les utilise pas comme
valeurs Pan et aucun rôle général de cache/fader n'est inféré.

Référence originale de 179229 octets, SHA-256
`fa1f83961bf68ba263eb4a146282456effa785afd12b1994dd6baf0048313086`;
référence modifiée de 179225 octets, SHA-256
`fc1f1efc5f4d4fd49af6561cd897b769ac00b856dd3f515227ed9854125f08ef`.
L'oracle est sélectionné par contenu et hash, pas par un basename ou nom
de dossier. Les pans témoins mono restent -50 et absent; Volume, Clip Gain,
clips/placements, routing/catalogue/identités média, 179 cibles logiques
et deux WAV conservés. Quatre cycles API byte-identiques, sans écriture
d'un candidat stéréo ni sauvegarde des références originales par l'agent.

Dix tests (8 synthétiques/2 natifs optionnels) vérifient valeurs/types,
absence mono, mono après stéréo, stéréo première/dernière/adjacente, noms,
identités/state/Volume, géométries et automation des deux canaux, refus
d'une autre piste malformée avant filtre, aucune mutation/cache et maintien
du périmètre mono des autres méthodes. **449 tests avec -W error**, syntaxe
Python 3.8 de 43 fichiers, 856 raises. Étape de lecture clôturée; aucune
validation d'écriture stéréo/automation revendiquée, aucune copie no-op
supplémentaire demandée à l'utilisateur.

### A.29 — validation de l'écriture stéréo §10.8

Diagnostic externe depuis l'original de A.28 : les deux payloads de lanes
statiques de C_STEREO sont préparés avant remplacement. Seuls les Int16 +26
passent -100→-50 à gauche et +100→+50 à droite. La gauche écrite est
byte-identique à l'oracle natif gauche; la droite utilise la même géométrie
statique vérifiée; la valeur R50 écrite est désormais validée dans Pro Tools.
Les états UI corrélés ne sont pas modifiés par supposition.

Candidat de 179229 octets, SHA-256
`c0100f0a4f9ca2957fed23b28d914d5bd0abb2898c1cdfda0676c31cc159d30b`.
Deux octets chiffrés changent (158851/158913, propres à cette référence),
deux cycles API byte-identiques, retour -100/+100 restituant toute la source.
Les 179 cibles logiques, UI, autres pans, Volume/Clip Gain/clips/placements,
routes/catalogues/identités médias et WAV restent exacts. Le script d'essai
ne remplace aucun original ou fichier resauvegardé. **Validation Pro Tools
confirmée le 10 octobre 2026** : L50/R50 dès ouverture, pendant lecture et
après sauvegarde/fermeture/réouverture. Save natif 179269 octets, SHA-256
`3f7b3eaa52cd66bf6f0a9872e09e062fe06a2b3433bd0ff549c363c5aca9d736`.
Autres pans, Volume/Clip Gain/clips/placements/routes/catalogues/identités,
179 cibles logiques et deux WAV inchangés. Deux cycles API exacts.
UI cible native : différences aux offsets 13/41/99/127, autres slots
identiques; ces champs ne sont pas écrits ni généralisés par le writer.

Après cette validation, branche stéréo intégrée à `set_track_pan()` selon
§10.8. Dix tests (7 synthétiques/3 natifs locaux optionnels), dont préparation
du second canal interrompue sans mutation, right=None, no-op conservant les
objets, frontières/signes, stéréo première/dernière/adjacente et refus des
autres profils. Le writer public reproduit exactement le candidat, la
gauche reproduit l'oracle natif gauche seul; cycles/compositions restaurent
intégralement les originaux et le save natif. **459 tests avec -W error**,
syntaxe Python 3.8 de 44 fichiers, 858 raises. Étape clôturée; aucune
automation Pan ou nouvelle règle de synchronisation UI intégrée.

### A.30 — validation de la lecture Pan mono §10.9

Paire native statique vers courbe créée dans Pro Tools le 10 octobre 2026.
Before 179269 octets SHA-256
`3f7b3eaa52cd66bf6f0a9872e09e062fe06a2b3433bd0ff549c363c5aca9d736`;
after 179348 octets SHA-256
`7b573e7fa0feda8100e905c13242c602a666df53a21144ab86bc59e3f60fa6a0`.
Piste A_OUTSIDE : Pan statique [(0,-50)] remplacé nativement par
**[(0,-50),(1729728000,-50),(1730208480,+50)]**. Le point initial implicite
est conservé, les deux points visibles sont 10:00:00:00 L50 et
10:00:10:00 R50 à 48 kHz/23.976. Payload 30→42, taille déclarée 20→32,
count 1→3 et segments 0→2; mêmes magic/flags/padding/réservés/terminateur.
After exact :
`0146010020000000000003000000020002000000000000000000ceff008e1967ceffe0e2206732000000`.

Clips/pistes/placements/Volume/Clip Gain/routes/catalogues/identités/WAV et
pans des deux autres pistes inchangés. UI cible Int32 +13/+99 passe de -50
à +50; autre UI exacte. Les 179 records de référence gardent leurs
métadonnées; trois chemins UI aux positions de record 4860/4949/5038
passent du child-index 1 à 2 pour 0x258b sous 0x2587/0x2551, tous les
autres composants/chemins inchangés. Ce réindexage natif n'est ni ignoré
dans la comparaison ni transposé à l'API. Quatre cycles API byte-identiques
sur les deux originaux; aucun authored PTX supplémentaire à ouvrir pour
ce seul lecteur. Treize régressions (10 synthétiques/3 natives optionnelles),
**472 tests -W error**, syntaxe Python 3.8 de 45 fichiers, **863 raises**.

Étape de lecture clôturée. Historique de l'essai d'écriture suivant : depuis
le before statique, reprendre la géométrie native de trois points et changer
seulement la valeur finale à R25. Candidat 179281 octets, SHA-256
`34422a3342446e4ac368cad25b42a7520e33ca0926af7ffd68e38f74eff598cb`.
Deux cycles exacts et retour au payload statique restituant tout
le before; UI/autres données/179 chemins de référence API et WAV intacts.
Retour positif utilisateur reçu, mais le save natif de 179263 octets,
SHA-256 `5fb1719d6eee812ef69e42502ccb65b9dc3edea56b1df4d9ce8460faa8a04b14`,
contient [(0,-50),(1729730002,-50),(1730208480,+25)]. Le premier point
visible est donc décalé de **+2002 samples / une image**, à 10:00:00:01.
Les autres données audio/UI/179 chemins de référence sont inchangés.
L'utilisateur confirme avoir déplacé ce point manuellement le 10 octobre.
Ce déplacement n'établit donc pas un bug ou une règle native de décalage.
Aucune compensation de timestamp ajoutée. La sauvegarde conserve ses
positions réellement éditées; intégration distincte du writer §10.10/A.31.

### A.31 — validation du writer Pan mono §10.10

L'utilisateur confirme fonctionnement et edit manuel du point d'une image
observé en A.30. Après clarification, `set_pan_automation()` reproduit
**exactement** le candidat d'écriture ouvert dans Pro Tools, avec les samples
initialement demandés. Il reproduit également le payload de l'oracle natif
de A.30 pour sa courbe L50→R50. Pas de correction supposée de sample/TC.

Save natif 179263 octets SHA-256
`5fb1719d6eee812ef69e42502ccb65b9dc3edea56b1df4d9ce8460faa8a04b14`.
Nodes [(0,-50),(1729730002,-50),(1730208480,+25)] préservés tels quels.
UI/autres pans/Volume/Clip Gain/clips/timeline/routes/catalogues/identités
et 179 chemins de référence égaux au before statique; deux WAV intacts.
Deux cycles API exacts, remplacement/compositions via le writer public
restaurant tout le save natif et retour au statique restaurant tout le before.
Treize nouvelles régressions (10 synthétiques/3 natives locales optionnelles) :
tri/types/limites/doublons/premier zéro/absence/stéréo/no-op/générateur et
préparation interrompus, invalidité d'autres pistes, mono après témoins
stéréo, reproduction native et compositions. **485 tests -W error**,
46 fichiers AST Python 3.8, **876 raises**. Étape mono clôturée.
La lecture stéréo suivante est corroborée séparément en A.32; aucune
écriture automatisée stéréo intégrée à ce stade.

### A.32 — validation de la lecture d'automation stéréo §10.11

Before natif 179263 octets SHA-256
`5fb1719d6eee812ef69e42502ccb65b9dc3edea56b1df4d9ce8460faa8a04b14`;
after natif 179277 octets SHA-256
`251e9172670f5b035113d39ca76fe3eac4811ee30e9aec6fecd202b67bf816c2`.
Sur C_STEREO, gauche [(0,-50),(1729728000,-49),(1730208480,+50)], droite
[(0,+50),(1729728000,+50),(1730448720,-50)]. La valeur du premier point
gauche visible est **L49 native**, pas L50 présumée; lecture sans arrondi.
Les fins à 10:00:10:00 et 10:00:15:00 corroborent l'association des deux
lanes. Les deux payloads sont 42 octets, magic/flags/reservés identiques
au profil mono, taille 32/count 3/segments 2/timestamps initiaux zéro.

Mono A_OUTSIDE (y compris son point édité d'une image) et B_INSIDE,
clips/pistes/placements/Volume/Clip Gain/routes/catalogues/identités/WAV
inchangés, **179 cibles de référence et leurs métadonnées exactes**.
UI natif : changements Int32 +13/+99 du mono et +13/+41/+99/+127 du
stéréo lors de lecture/save, sans modification de ces champs par l'API.
Quatre cycles API byte-identiques sur la paire, lectures sans mutation/cache.
Dix tests (8 synthétiques/2 natifs optionnels) : canaux/retours frais, stéréo
première/dernière/adjacente, erreurs du canal non sélectionné/autres pistes,
absence/profils inconnus/types et contrats mono/writer/statique préservés.
**495 tests -W error**, 47 fichiers AST Python 3.8, 876 raises inchangés.
Étape de lecture clôturée; aucune nouvelle copie no-op à ouvrir.

### A.33 — écriture d'automation stéréo validée, étape 30 clôturée

Depuis le before de A.32, reprendre les géométries complètes des deux
enveloppes natives, préparer tous les payloads avant remplacer les lanes.
Valeurs Int16 des trois nœuds : gauche -25/-25/+25, droite +25/+25/-25;
timestamps natifs/header/count/flags/terminateurs exacts, UI non écrite.
Candidat 179287 octets SHA-256
`3cd1e1325fd23b6c16a635c78017a4b37998a900df5b7c7c862e9a2363ce3b3a`.
Deux cycles API exacts, retour aux deux payloads statiques restituant tout
le before. Autres courbes/données audio/UI/médias/WAV/179 références intactes.
À la préparation, validation ouverture/lecture/save/réouverture Pro Tools
attendue avant intégration : aucun writer public automatisé stéréo alors.
Aucune nouvelle règle UI ou arité de layout supposée. Retour final ci-dessous.

Retour de validation : dernier point droit observé à 10:00:14:22.
Le candidat original reconstruit retrouve son SHA-256 exact ci-dessus et
1730448720 samples (10:00:15:00). Le fichier resauvegardé, 179319 octets,
SHA-256 `253450b7032a89699a94ebdc9cf4670f6e69a0e421065983d6140dc94da21fad`,
contient 1730444716, soit -4004 samples/deux frames; la courbe gauche reste
exacte. Origine de la modification non établie. Cette observation ne
justifie aucune correction de conversion ou compensation des timestamps;
écriture stéréo publique toujours non intégrée.

Une manipulation manuelle reste possible mais non confirmée par l'utilisateur.
Un retest reproduit le candidat initial byte-identique (même SHA-256), dans
un dossier distinct sans remplacer la sauvegarde native. Relever la position
à la première ouverture avant sauvegarde, puis après save/réouverture sans
edit si elle est exacte; cette comparaison était alors en attente.

Retest finalement confirmé conforme par l'utilisateur, qui attribue le premier
décalage à une manipulation. Sauvegarde native du retest : 179369 octets,
SHA-256 `6397b241604871575c0dd4cad061f14ae01d26cbd2abba814046f74707fff6ea`.
Gauche [(0,-25),(1729728000,-25),(1730208480,25)], droite
[(0,25),(1729728000,25),(1730448720,-25)] exactes; autre audio/Volume/Clip
Gain/routes/catalogues/placements/WAV/179 références conservés. Différences
natives UI opaques conservées, pas transposées au writer. Deux cycles natifs
API exacts, no-op conservant l'objet et compositions restituant tout le fichier.
Writer public §10.12 reproduisant le SHA-256 exact du candidat ouvert.
Sept tests (5 synthétiques/2 natifs locaux), suite **502 tests -W error**,
48 fichiers Python 3.8, 874 raises; aucune correction compensatoire.

### A.34 — suppression de markers, étape 31 clôturée

Oracles 48 kHz/25fps issus de la référence validée étape2 : before192436,
SHA-256 `0181f92b23202a0fd4ace48b11b98c0b372db9e0156f68ae24eff0368115339d`;
after suppression index2 :192156,
`533a2784ac8edf0df38006ec0304472b0a3f15ba9c427bb506892927374e8ed7`;
after suppression des trois :191536,
`b73602f64731e2a1a4c0283e5797eea89aea02de165df31865f1256d15fc02c4`.
Survivants index1/M25_FRAME24 et3/M25_MINUTE : structure exacte excepté UUID
secondaire +23..38 renouvelé nativement. Ces changements et les autres
différences natives opaques de catalogues/UI/état ne sont pas copiés au writer.
Forme vide native brut12 nul, trailer initial huit zéros conservé. Les lectures
pistes/clips/placements et les deux WAV restent identiques. Six cycles no-op
byte-identiques. 167 records standards, aucun ne cible les markers retirés;
la purge d'un tel record est donc prouvée **synthétiquement**, pas par l'oracle.

Sorties API isolées : suppression index2,192131 octets,
SHA-256 `9c472a3465f61cac51768d9314ac0d51f593e7d4bf3365d474ea664137631211`;
suppression trois,191530 octets,
`b67aa61da76bec01b1de644bad6a251d0e711f813464e780b59dd39303332e17`.
Tous les autres roots exacts avant save, 167 cibles/métadonnées préservées,
deux cycles par sortie exacts et WAV intacts. Conservation des UUID de source,
pas restitution de tout le after natif malgré ses modifications annexes.
Treize régressions (9 synthétiques/4 natives), dont records construits vers un
marker/descendant supprimé et un survivant, purge/no-op/rechargement, erreurs
tardives atomiques, trailer non nul synthétique, noms partagés/ET/ordre inchangé,
deux cadences/deux autres sample rates synthétiques sans conversion. Suite515,
49 fichiers Python3.8,889 raises. À ce stade les sorties API étaient encore
à valider dans Pro Tools; retour final ci-dessous.

Validation utilisateur ouverture/lecture/save/réouverture des deux sorties
confirmée. Saves natifs : partiel192137 octets,
SHA-256 `dcc67be7280d067f3d6dd8009add13a82ec0d9718fd5d328b24fc017137ab5a1`;
vide191539, `af98586bb4b1d09b9809333f32c0fa2fe379ead40b2b1e788430c2e72aa2a807`.
Survivants1/3 byte-identiques au before, y compris UUID originaux; forme vide
exacte, audio/clips/placements/WAV/167 cibles et metadata conservés. Deux
cycles API par save natif exacts, no-op gardant la liste d'items et composition
supprimant les points restants sans renumérotation. Deux régressions natives
supplémentaires : quinze pour la suppression, total517 tests -W error,
49 fichiers Python3.8 et889 raises inchangés. Aucune extension de profil.

### A.35 — suppression de placement audio, étape32 clôturée

Deux paires natives 48 kHz/25fps, une piste Audio1 et définition Audio1_01
de192192 samples à1728000000; second before possède un fade-out de96192
samples, geometry29/type2/UInt24 répété. Sources : sans fade75890,
SHA `e649d79a327f1ca9fea4b9073af9956d1dbcb9535b6d49af592a6ce531925fbf`;
avec fade76062,
`8d40c1dbfdf04413ac89da9dddcdfdc437fc319d413aba5e15d159cf739e55b0`.
Afters suppression : sans fade75923,
`ccb329a8ff62d34b87122b2372c5a2e04d753cab3f8e63d189ec04efb5bcb23e`;
avec fade75954,
`9059d0593904e512e7b0d9594d1d70d0b90ae6e58143c4ae9d833a3601600e28`.

Les deux timelines sont vides, la piste et la définition/bin/WAV conservées.
Playlist native brute `[longueur_nom, nom, compteur0, trailer0100]`;
root2630 conserve son compteur4 nul. Le fade-out et sa géométrie disparaissent
ensemble : contrairement au writer contrib examiné, aucune géométrie
orpheline n'est nécessaire. Les différences de définitions/identités et
autres racines natives restent opaques, pas transposées au candidat.
Les quatre oracles passent huit cycles API byte-identiques.

Sorties API : audio seul75834,
SHA `1cf7c562c910ad300979528e24be97c260049968d2f3745d45866bd01729ae9c`;
audio+fade75912,
`6cb091268e66cf85a586b038bcf85db6d3cb74ef336c299a2f698c9a9301cbc0`.
Roots1054/2630 exacts aux oracles, autres roots byte-identiques avant save,
WAV exact et167 cibles/métadonnées TOC préservées, quatre cycles de sorties
exacts. Aucun record natif ne cible les événements supprimés; la purge des
records vers événement/descendant/géométrie et la relocalisation du survivant
sont donc couvertes **synthétiquement**, pas prouvées par ces oracles.

Quatorze régressions (11 synthétiques/3 natives), suite531 avec -W error,
50 fichiers Python3.8,907 raises. Fixtures locales individuellement ignorées.
**Ouverture/sauvegarde/réouverture des deux sorties dans Pro Tools encore
attendues** : pas de support publié ni de compaction multi-géométrie,
suppression de crossfade ou nouveau profil fade déduit.

Retour utilisateur du10octobre : les deux séquences API sauvegardées.
Saves reçus : sansfade75894,
SHA `433c15d14a3d94a802f8ccc796a958db39476c3dd317a18a504d5ea88902af0e`;
avecfade75934,
`ecbee253d0390a61ec740c2558325da16e0eb01a3f812cc891f85de5c024d08f`.
Roots1054/2630 exacts à la forme native vide, gain exact, lectures bin/longueur/
offset et catalogue filename/link ordinal0 inchangés, WAV exact,167 cibles et
métadonnées de records préservées. Les bin/catalogues ont des identités opaques
rafraîchies nativement : ne pas les déclarer byte-identiques à la source, ni
les recopier au writer. Quatre cycles supplémentaires byte-identiques, sources
et saves protégés. Deux régressions natives ajoutées : seize pour la suppression
(11synthétiques/5natives), **533 tests -W error**,50 fichiersPython3.8/907raises
inchangés. Ouverture/save corroborés; réouverture non explicitement déclarée
dans ce retour initial. **Confirmation finale reçue le11octobre2026** : les
deux sorties ont bien été fermées puis rouvertes après sauvegarde. Étape32
clôturée; aucun changement de writer/géométrie/code ni nouvelle fixture depuis
les saves déjà contrôlés. Les refus crossfade/multi-géométrie restent en vigueur.

Point suivant préparé : deux trims natifs indépendants depuis la référence
compacte validée (§5.1), offset100, un clip sansfade à1729728100–1738408672,
longueur8680572, 48kHz/23.976. Sélections demandées : head
[1729728200,1738408672] (offset attendu200), tail[1729728100,1738408572]
(offset100 conservé); longueurs8680472. Copies exactes des PTX/WAV et source
protégée, aucun writer trim en place intégré avant réception/comparaison.
Ces futures écritures sont distinctes du trim historique créant un sous-clip;
aucune sémantique temporelle proposée par la contribution n'est inférée.

### A.36 — trims en place, étape 33 validée

Oracles 48kHz/23.976, piste Audio1/clip Audio1_01-01 : before171594,
SHA `32e57df19dac2c4113c187f5a77831783868047b77cbf3795726032d6d511f54`,
start1729728100/end1738408672/length8680572/offset100. Head171875,
`6b2b91e757bc8555523b3dce47e10e5816c7adacb168f7b4f9997e386974f4d4` :
start1729728200/end1738408672/length8680472/offset200. Tail171587,
`cf3efb566084e50a1da3da0607c8b57b78701fcb99e94fc293ffffa687f6dfcc` :
start1729728100/end1738408572/length8680472/offset100.

Type4/2628, tailleA+64, header compact exact; head modifie sourceA+5,
longueurA+6/3 et deux UInt32A+9/A+13; tail seulementlength. Queue47 exacte.
Événement head+7 avance de100, tail exact. Pro Tools conserve pour le head
une copie supplémentaire de l'ancien clip avec le même nom (ordinal2,
offset100/longueur8680572); le clip placé reste ordinal1. L'oracle head a
trois définitions, le tail deux. C'est une différence native du bin, pas une
erreur de nommage utilisateur. Les comparaisons suivent les références de
timeline pour cet oracle; les mutations publiques refusent les homonymes.

Candidats API171594octets chacun : head
SHA `f2b7f0eb282099300145fe61d45d5980dbe449f28619f0fef17d9c8ae4f3d3ac`,
tail `2dd2c344200dbf82d10f8f7538c6f560ee72e21df9076f1ea4a2c550c0c27860`.
Payloads cibles/événements byte-identiques aux natifs; cinq bytes décryptés
head (7904/7905/7908/7912/152886), un byte tail7905 seulement, tous dans les
champs corroborés. Aucun backup alloué, nom/ID/UUID et parents conservés;
autres roots/WAV/167 cibles et métadonnées TOC exacts. Huit cycles des quatre
copies natives, quatre cycles de sorties exacts; originaux protégés.
Onze tests (8synthétiques/3natifs), **544 tests -W error**,51fichiersPython3.8,
921raises. Matrices inputs/bounds/width/unknown/sentinelle/gain/hidden/fades,
objets/survivants/atomicité tardive/compositions; trims historiques AST inchangés.
**Validation des deux sorties confirmée par l'utilisateur le 11 octobre 2026**.
La différence de départ communiquée pour tail était une erreur de transcription,
pas un déplacement du clip. Aucun correctif de timestamp ajouté. Les saves
Pro Tools font 171558 octets chacun : head SHA
`055a11bddb2a73172a3623e2cb646679c41c13b4cd75d5f421a3a7aaef79e8dc`,
tail SHA `8b0edc58afd5c6b53574b758a1d3ea561565d613c6e6e64fedfe733e48dcdc05`.
Géométries exactement attendues, deux définitions et parent inchangés en lecture,
WAV byte-identiques, 167 cibles et métadonnées TOC identiques. Cela ne signifie
pas que tous les champs opaques du catalogue sont identiques au before.
Deux régressions natives supplémentaires : quatre cycles exacts après save,
compositions head +25 / tail -25 sans allocation; treize tests sur ce writer,
suite **546 tests -W error**, 51 fichiers Python 3.8, 921 raises inchangés.
Pas de support général des autres familles/largeurs/fades/timestamps déduit.

### A.37 — conservation de plages, étape 34 validée et intégrée

Before = A.36. After natif reçu le 11 octobre 2026, 172486 octets SHA
`3526700422b155b6c512daf8a7120b903ae2b909fd5e07641e3a851ea175f156` :
deux placements [1729728100,1729776100) et [1729872100,1729920100), 48000
samples chacun, offsets100 et144100, WAV byte-identique. Bin cinq définitions
(parent, deux retenues, deux supprimées), références timeline ordinaux1/3;
167 records/cibles et métadonnées TOC exacts, quatre cycles natifs exacts.

Géométrie générale corroborée pour ces deux fragments, `A=4+name_length` :

| Champ | Fragment source UInt8 | Fragment source UInt24 |
|---|---|---|
| Header `A..A+4` | `01 10 20 44 08` | `01 30 20 44 08` |
| Offset source | UInt8 à `A+5` | UInt24 à `A+5` |
| Longueur | UInt16 à `A+6` | UInt16 à `A+8` |
| Deux timestamps | UInt32 à `A+8`, `A+12` | UInt32 à `A+10`, `A+14` |
| Queue historique sans gain | 47 octets à `A+16` | 47 octets à `A+18` |

Les deux timestamps valent le début de chaque placement; aucun FFFFFFFF
imposé. La queue est exactement celle de §6.7 : **pas de UUID à son extrémité**.
Identité raw48 du `0x2629` : octet22 FF, UUID aux octets23..38 inclus.
`0x4403` opaque identique, raw104 natif conserve sa structure et son index
média; refresh temporel opaque de save non repris. La paire ne corrobore pas
le tail104 C5/counter magique du contributeur. Son helper fixe les largeurs
du prototype, remplace les 16 derniers octets audio par un UUID et impose
une sentinelle : ne pas réutiliser ces writes sur ce profil. L'ancien
`create_subclip()` écrit identity22..37 : différence avec la zone observée
ici à auditer séparément; son writer reste inchangé, pas certifié par cet essai.

Candidat **externe isolé**, aucune méthode `keep_clip_regions()` intégrée :
171949 octets SHA `dcb02dc8ecf9109cbaa6d34931c8b29b632cede3df89dec76e5f00eb778b0da5`.
Premier nom/ordinal1 conservés, deuxième nom généré/ordinal2/UUID frais dans
raw48, attributs audio exacts aux natifs et événements exacts hors ordinaux.
Les deux payloads sont construits depuis le prototype intact, sans double
delta. Trois définitions uniquement (pas d'allocation des morceaux supprimés),
compteurs bin/playlist ajustés; parent/raw104/4403/autres roots/WAV et 167
cibles intacts. Aucun record TOC ajouté/purgé, gain/fade/media non modifiés.
Deux cycles du candidat exacts. Trois régressions natives (géométrie,
attributs/identités/IDs/roots et six cycles exacts), suite **549 tests -W error**,
52 fichiers Python 3.8, 921 raises inchangés à la préparation du candidat.
Validation utilisateur reçue le 11 octobre 2026. Save Pro Tools171920,
SHA `03efc9fdd12272d1652a8e945499503ccc6c76af3d8b01f15edc24c8ecdca064` :
deux placements/offsets exacts, WAV/trois définitions/167 cibles conservés,
deux nouveaux cycles byte-identiques. `keep_clip_regions()` §6.8 intégré,
reproduit le candidat byte-identique à UUID fixé. Dix-sept tests writer
(15synthétiques/2natifs) ajoutés, vingt au total pour cette étape : entrées/
profils/timestamps/widths, trois fragments/UTF-8/rates, compositions, collisions,
macro-ID/hidden/overlap/fades, UUID et overflow tardifs atomicité/objets/metadata,
reproduction et cycles/save natif. **566 tests -W error**,53fichiersPython3.8,
950raises. Autres profils non corroborés et anciens sous-clips restent distincts.

### A.38 — import média PCM24, étape35 clôturée et intégrée, non publiée

Before/after préparés depuis A.36 avant édition, un média et un clip virtuel.
WAV à importer mono PCM24/48k, même fmt/data que l'original, UMID/stem nouveaux,
référenceBWF1729728000, durée8680672. SHA
`b603d8c5e57d015eaa7f630de9eccaacc2de8741b806ad4ddfd2bcbce7b87031`.
Demande native : Copy dans Audio Files/Clip List puis placement complet
1738456672→1747137344; ancien clip1729728100→1738408672 conservé. Aucun nouvel
importeur générique intégré. Validator103a du CLI contrib passe sur ce before
valide; cela ne certifie pas les writers2106/2628 ni leurs limites/troncatures.

After natif172232octets SHA
`d467d1d77026f1ba2b7372fd39d8a7467ce26c7c04b06c625bd9204d10417fac` :
ancien clip intact, nouveau parent/événement aux positions demandées,
bin3/catalogue2/167cibles. La copie WAV est byte-identique à l'entrée.
Profil observé (distinct du builder float32, writer public strict en §6.9) :

- `0x2628` parent type4, préfixeA=`00 00 30 44 00`, tailleA+55;
  duréeUInt24 A+5, deux référencesBWF UInt32 A+8/A+12, slotA+36..47
  nul dans le nouveau parent. La référenceBWF n'est pas le placement timeline.
- `0x1001`31octets : format mono24/48k au préfixe, duréeUInt24+6,
  identité9octets+22=`2a` suivie deUMID[16:24]. Autres champs opaques conservés.
- `0x2106` raws151/58 séparés par0x4301 : filetimesUInt64+29/+105,
  BWFUInt32+100, UUID+135..150 et UMID32octets tail+18..49.
- Raw48 du0x2629 : premierUInt32=indexmédia(1), **pas ordinalbin(2)**;
  UUID23..38. Raw104 : indexmédiaUInt32+96, reste opaque conservé.
- Compteurs103a incrémentés, recordnom+EVAW ajouté avant trailer;
  compteur1004/ordinal1003/bincount et événement35/tail000101 ajoutés.
  Les deux timestamps de définition restentBWF; événementUInt64+7=placement.

Candidat externe isolé172278octets SHA
`0c0408dc85ef01fb44e026c6a211e55d8176ff82fd6971a68fea43a5f8ead7d3` :
payload nouveau parent et géométrietimeline conformes à l'oracle, identitésWAV
corroborées, anciensrecords/clip/WAV inchangés. UUID/filetimes générés selon
les helpers existants; labels de chemin historiques conservés (pas copiés
depuis le dossierafter). Deux cyclesAPI exacts; quatre régressions natives
lecture/conservation/identités/cycles, suite570 à ce checkpoint.

Retour utilisateur « all good lets go » : ouverture/lecture/save/réouverture
réussies. Save172230octets SHA
`934718901de9cf2fa91e1f924e65dd38668af11701b8d4c834c9b0fdf744e171` :
deux placements/source/catalogue/167cibles exacts, deux cyclesAPI byte-identiques.
`import_audio()` §6.9 intégré après ce retour, reproduit le candidat validé
byte-identique àtime/UUIDfixés.18tests (16synthétiques/2natifs) couvrent imports
successifs/Unicode/UInt64placement, identités et refusatomiques tardifs, savePT.
Suite588/55Python3.8/984raises. Aucun profil ajouté au builder, aucun support
universelPCM24/modernes/MXF annoncé, aucune session originale écrasée.

### A.39 — issue contributeur 1, composition nommage AAF / Volume clôturée

Le troisième apport matérialise le nom de la seconde playlist mono de la
paire native mixte; save natif175822 octets/SHA
`056f01c9815d06a1d85ae2ee002900cb4e28f8eac3f1248c078ae4930bcb3e4b`.
Noms AAF_RENAMED_LONG et B_INSIDE, starts1729728000 et1730208480,
longueurs1355354, offsets0;172ciblesTOC, médias et deux cycles exacts.
Ces noms/positions ne sont pas des préconditions du profil.

Le quatrième apport utilise le resolver Volume existant après ce nommage :
un point B à1730448720/10:00:15:00/+6dB, A conservée à0dB.
Ouverture/lecture/save/réouverture confirmées. Save natif175874 octets/SHA
`957f05f7a4634b245ded0c5b00b340882fe6ce8da911a3e72dbebf3c9966dbe0` :
point B réellement à1730450722/10:00:15:01/+6dB. **L'utilisateur confirme
avoir déplacé ce point manuellement**. Conserver la position observée;
ne pas la traiter comme normalisation du writer ni oracle de compensation.
Volume A, autres lanes/routes brutes, bin/clips/placements/longueurs/offsets,
markers/groups/media et172ciblesTOC conservés; deux cycles API byte-identiques.

Vingt tests de nommage/composition (16noms/4Volume), dont les deux sauvegardes
Pro Tools privées optionnelles; suite638/60Python3.8/1001raises. Pas de
nouveau writer Volume ou scan GUID/cache; pas d'oracle AAF anonyme stéréo
manuel, de géométrie moderne de marker ou de conversion AAF universelle.
Les quatre apports sont clôturés seulement dans le périmètre documenté.

## 13. Catalogue exhaustif des erreurs

La portée d'« exhaustif » est la suivante : toutes les familles d'échecs explicitement détectées ou propagées par le code courant de `pt_api.py`, ainsi que tous les messages Pro Tools consignés dans le corpus et l'historique des essais du projet. Elle ne prétend pas recenser les messages possibles de toutes les versions de Pro Tools.

Le source courant contient 1002 instructions `raise` : 844 `ValueError`, 92 `TypeError`, 20 `NotImplementedError`, 21 `OverflowError`, 8 `FileNotFoundError`, 3 `FileExistsError`, 1 `OSError` et 13 relances nues de l'exception originale. L'`OverflowError` interne supplémentaire du calcul rationnel est converti en `ValueError` de plage par les méthodes publiques temporelles.

### 13.1 Messages observés dans Pro Tools

| Message affiché | Causes techniques couvertes | Prévention/réparation |
|---|---|---|
| **Magic ID does not match** | `0x0001` pointe au mauvais offset; un enregistrement standard ou un offset secondaire de `0x0002` est obsolète; un bloc a été supprimé sans purger son pointeur; une relocalisation a utilisé un offset dupliqué; un miroir brut de suppression de piste a été conservé ou mal compacté. | Recalculer tous les offsets, patcher `0x0001`, relocaliser `0x0002`, purger exactement les enregistrements et groupes de métadonnées natifs supprimés, puis refuser les `original_offset` dupliqués. |
| **Unexpected stream type** | Un faux bloc a été créé par un `0x5A` fortuit; `block_type`, taille ou `content_type` ne correspondent plus au flux attendu; l'ordre ou l'enveloppe d'un bloc a été altéré. | Garder les payloads fixes à plat, conserver les octets opaques, sérialiser l'en-tête générique dans l'ordre exact et ne générer que les dispositions vérifiées. |
| **End of stream** | Bloc/payload tronqué; taille déclarée trop grande; compteur de `0x0002`, `0x1054`, `0x1052`, `0x2030`, `0x2424`, `0x2426`, `0x262a`, `0x262c`, `0x2630`, `0x2637` ou `0x260a` supérieur aux données réelles; compteur de série `0x0002` non ajusté; table `0x0002` amputée par un faux enfant; ajout de padding d'alignement; longueur de nom décalant la queue. | Valider toutes les tailles et compteurs avant mutation/sauvegarde, ne jamais aligner artificiellement les chaînes/blocs, garder `0x0002` plat et retirer uniquement les records standards ou groupes de métadonnées explicitement vérifiés. |
| **Cannot open the selected file because end of stream encountered** | Variante d'interface du même échec **End of stream**, observée lors des premiers essais de dissolution de Clip Group et de crossfade dont la structure sérialisée était incomplète. | Appliquer les mêmes contrôles que pour **End of stream**, en particulier compteurs, padding, payloads fixes et intégrité complète de `0x0002`. |

### 13.2 Exceptions de l'API

Extension non publiée des commentaires de marqueurs (§8.3), sept nouveaux
sites de `raise` : `TypeError` pour `include_comments must be a Boolean.` ou
`Marker comment must be a string.`; `ValueError` pour `Marker comment cannot
contain a null byte.`, `Marker comment must be valid UTF-8.`, `Marker comment
is too long for the PTX format.` et `Invalid UTF-8 marker comment.`.
Le site `Unsupported marker comment profile (...)` regroupe : legacy
one-property shape required, child geometry, point payloads, primary bounds,
point timestamps, primary length, secondary length, UUID envelope, comment
copies disagree et NUL in comment. Aucun retour partiel/texte vide silencieux;
les refus d'ajout précèdent les mutations. Les autres erreurs d'index/nom/TC,
de règle/playlist/population et les exceptions natives de save restent propagées.

| Exception | Conditions exhaustives par famille |
|---|---|
| `TypeError` / `ValueError` / `NotImplementedError` / `OverflowError` — importPCM24 | 34sites nouveaux (§6.9). Paramètres : nomsnon-str, vides/NUL/nonUTF8/trop longs; startnon-int/bool ou horsUInt64, endoverflow. Profilendian/rate/enum inconnu : `Import requires the verified little-endian 48k/23.976 profile.`; pathhorsAudioFiles, extensionnonwav. WAV : RIFF/taille/chunksuniques/bornes/alignement viahelperscommuns; `Import requires native mono PCM24/48 kHz fmt40.`; dataunique/nonvide/aligné3; `PCM24 import duration must fit UInt24 samples.`; BWFUMIDnul/prefixnonSMPTE/BWFhorsUInt32; regnUTF8/stem/durée/refs/identités discordants. Cible/prototypeabsents/ambigus; directblockmanquant/dupliqué; ciblefade/groupe/layoutinconnu (`NotImplementedError`); bin/compteur/type/namedrecord/parent48/104/media31/151/58/identité/timing inconnus ou incohérents. Filename/clipname/mediaidentity/UMID déjà enregistrés; compteurs103a/EVAW/trailer incohérents; overflowbin/media/names. `Imported UUID collision; session remains unchanged.`. Les erreursIO de lectureWAV (FileNotFoundError/OSError), générationUUID/filetime/helpers/playlist se propagent avantmutation; aucune copie/audio/save implicite, aucun échec ne modifie l'arbre ou le WAV. |
| `TypeError` / `ValueError` / `NotImplementedError` / `OverflowError` — conservation de plages | `clip_name must be a string.`; nom vide/NUL/non UTF-8. `regions must be an iterable of sample pairs.`; `Each region must be a pair of integers.`; `Each region must contain start and end.`; `Region bounds must be integers, not bool.`; `Region bounds must be increasing UInt32 positions.`; `regions must not be empty.`; `Regions must not overlap.`. `Keeping regions requires the verified little-endian profile.`; `Keeping regions requires exactly one main audio placement.`; `Keeping regions does not support hidden or alternative events.`; `Unsupported region-retention event profile.`; `Unsupported region-retention placement bounds.`; `Every retained region must lie inside the original clip.`; `Keeping regions does not support overlapping audio placements.`. Erreurs des helpers de maps/définition unique/compteurs/bindings propagées. `Unsupported region-retention clip list structure.`; `Unsupported region-retention definition structure.`; `Unsupported region-retention audio payload structure.`; `Unsupported region-retention clip header or source offset.`; `Keeping regions requires the exact no-gain tail and defined unshifted timestamps.`; `Unsupported region-retention identity or media-link profile.`; `A generated region name already exists.`; `Invalid region-retention playlist counter.`; `Retained source offset and length must fit the verified UInt24 profiles.`; `A generated region UUID already exists.`. NotImplementedError : `Keeping regions on a playlist containing fades is not verified.`. OverflowError : `Region-retention clip count exceeds UInt32.`. Exceptions natives de l'itérable/paires/encodage/UUID/clonage propagées; tous payloads/clones/compteurs préparés avant affectation, aucun objet/metadata changé sur erreur, aucun save ou WAV implicite. |
| `TypeError` | Chemin non path-like ou résolu en `bytes`; tampon non `bytes`/`bytearray`; enum/composants/timecode/timestamp/index non entiers; paramètres `mute`/endianness/`include_fades` non booléens; champs `PTBlock` ou `base_offset` du mauvais type; item d'arbre non `bytes`/`bytearray`/`PTBlock`; nom attendu non `str`; gain/volume non convertibles en réel; offset/longueur de sous-clip ou montant de trim non entier; type/forme de fade non `str`; `clip_specs` non itérable ou fourni comme une chaîne, des octets, un chemin ou un mapping unique; descripteur individuel non mapping; `track_name`, `physical_filename`, `clip_name` ou `group_name` non `str`; `placement_start_samples`, `start_samples` ou `length_samples` non entier ou booléen; `session_name` non `str`/`None`. |
| `ValueError` — enveloppe et temps | Chemin vide; fichier/en-tête trop court; signature/version/endianness/mode XOR invalide; delta XOR introuvable; sample rate non fini, non positif ou hors UInt32; cadence inconnue; composant/timecode/drop-frame invalide; sample négatif; conversion temporelle non représentable. |
| `ValueError` — arbre, parsing et sauvegarde | `block_type`, `content_type`, tailles ou offsets hors bornes; profondeur >128; cycle; `original_offset` dupliqué; `0x0001` absent/invalide/mal placé; `0x0002` absent/dupliqué/non final/non EOF/non plat/vide; liaison `0x0001→0x0002` fausse; record/suffixe/compteur de série `0x0002` invalide; cible de pointeur inconnue; relocalisations chevauchantes; métadonnées `0x1028`/`0x204d` absentes, dupliquées ou tronquées. |
| `ValueError` — pistes et événements | Racines `0x1054` ambiguës; compteurs `0x1054`/`0x1052` incohérents; header, nom UTF-8, compteur ou structure `0x1050→0x104f` invalide; queue audio inconnue; ID de clip timeline inconnu; piste/placement absent ou ambigu; timestamp cible hors champ de stockage; pool de pistes dans le profil UI pré-normalisation observé, qui doit être ouvert et sauvegardé une fois dans Pro Tools avant son premier renommage. |
| `ValueError` — noms de playlists anonymes/mixtes et remplissage | `Track name cannot be empty.` dans les validateurs de mutation; `Unsupported anonymous main playlist header.`; ou `Track name cannot be empty: unverified anonymous mono track catalog (...)` (libellé historique conservé, y compris pour l'identité Audio stéréo). Raisons : racines `0x1015`/`0x2107`/`0x2519`/`0x2624` absentes/ambiguës, champs non bruts, longueur de nom tronquée/invalide, UTF-8/NUL, compteurs descripteurs/métadonnées/miroirs/slots, structure/largeur/index de canal répétés/ordinaux de descripteur, noms dupliqués, nom/identité metadata contradictoire, identités dupliquées/nulles, familles de miroirs absentes/mal ordonnées, miroir vide, type/nom/identité/ordre/largeur inconnus, chemin/identité/ordre de slot inconnus. Nouveaux contrôles : `Audio channel identities do not match the main playlist count.`; `Main playlist name disagrees with verified Audio identities.`; `Playlist naming requires the verified little-endian profile.`; `Playlist name is too long for the PTX format.`. Les erreurs du parser partagé de map/header/événements sont propagées; prévalidation/préparation intégrales avant mutation. Le refus global `Mixed named and anonymous main playlists are not supported.` est retiré au profit de ces invariants; aucune omission de piste ni fallback count-only. |
| `ValueError` — lecture des sorties bus | `Unsupported mono bus track-output profile (...)` (libellé historique conservé) : identité Audio mono non vérifiée; noms playlist/catalogue contradictoires; payload brut absent; longueur de nom tronquée/invalide, UTF-8 ou NUL; catalogue live `0x2603` absent/ambigu, compteur incohérent ou chemins imbriqués; conteneur de sortie principal absent/ambigu; nombre/position de `0x260e` hors profil à un descripteur; header, réservés ou trailer de sortie non vérifiés; bus sélectionné absent ou homonyme; géométrie mono autonome invalide; identité ou largeur en désaccord avec le descripteur; alias d'identité parmi les chemins mono/stéréo reconnus. Pour la nouvelle géométrie : `Unsupported bus track-output profile (selected path is not a verified autonomous stereo bus).` en cas de préfixe, taille 44, largeur 2, champ autonome/bus, magic, identité non nulle ou suffixe zéro invalide. Les erreurs de map `0x1054`/`0x1052` restent propagées. Aucune liste partielle, aucune mutation. |
| `TypeError` / `ValueError` — réaffectation mono bus | `track_name`/`output_name` non `str` (`TypeError`), vide/NUL/non encodable en UTF-8 (`ValueError`); tous les refus de map principale nommée et du validateur commun des sorties; `Unsupported mono bus output descriptor block type.`; `Conflicting descriptors for an already-used mono bus.`; `Ambiguous code shared by different mono bus outputs.`; `Track not found in verified mono Audio outputs: ...`. Pour une cible non affectée : `Unsupported unused mono bus output catalog block types.`; `Target mono bus is missing or ambiguous in the live catalog: ...`; géométrie mono autonome invalide via le helper partagé; `Ambiguous identity for the unused mono bus target.`; `Unused mono bus code calibration requires at least two distinct assigned buses.`; `Inconsistent or negative mono bus code calibration.`; `Calibrated mono bus output code is outside UInt8 range.`. Tous les refus précèdent le remplacement unique, sans mutation ni fichier créé; appeler `save()` séparément. |
| `ValueError` — transition de bus stéréo | Tous les refus communs de réaffectation/calibration précédents s'appliquent aussi aux noms de bus stéréo. `Bus routing requires two verified direct pan containers.` pour compte/type/emplacement des pans inconnus; `Mono-to-stereo requires the verified empty pan containers.`; `Stereo-to-mono requires the verified centered static pan lane.` et `Stereo-to-stereo requires the verified centered static pan lane.` (message construit selon la largeur cible); pour réduction vers mono seulement : `Stereo-to-mono requires an unambiguous pan pointer table.`; `Stereo-to-mono requires a nonempty standard pan pointer table.`; `Stereo-to-mono pan lane has unverified pointer references.`. Les refus propres au payload/layout `0x0002` sont également propagés dans cette réduction. Alias d'identité contrôlés entre les deux géométries. Tous les refus précèdent les mutations du route/pan/métadonnées de suppression, aucun fichier créé. |
| `TypeError` / `ValueError` / `NotImplementedError` — suppression de pistes | `track_names` est une chaîne, n'est pas itérable, est vide, contient un nom non `str`, vide, NUL ou dupliqué; nom absent; tentative de supprimer toutes les pistes; miroir unique `0x1054`/`0x1015`/`0x2107`/`0x2519`/`0x2587`/`0x2624`/`0x202b` absent, ambigu, tronqué, mal ordonné ou incohérent; liste/compteur/ordinal/pointeur brut `0x0002` non natif; ou playlist contenant un événement. Le dernier cas retourne `NotImplementedError`; tous les autres rejets se produisent avant écriture. |
| `TypeError` / `ValueError` / `NotImplementedError` — suppression de placement audio | `clip_name must be a string.` (TypeError); vide/NUL/non UTF-8 (ValueError); `Clip deletion requires the verified little-endian profile.`. Erreurs des helpers de définition unique/maps/playlists/événements/bindings propagées. Clip absent/non placé/placements multiples; `Unsupported clip-deletion event payload length.`; `Invalid clip-deletion placement bounds.`; `Unverified fade association for clip deletion.`; `Attached fade-out exceeds the deleted clip.`; `Attached fade-out ownership is ambiguous.`; `Unsupported single fade-out deletion structure.`. Erreurs du décodeur UInt24 répétition/marker/mode/durée propagées. `Clip deletion requires a unique 0x0002 pointer table.`; `Truncated clip-deletion 0x0002 pointer table.`; records invalides; `Conflicting removed clip-event block types.`. NotImplementedError : `Deleting a clip linked to a crossfade is not supported safely.`, `Clip deletion with multiple fade geometries requires a native compaction reference.`, `Clip deletion supports only the verified single UInt24 fade-out.`. Toutes les préparations/validations précèdent les affectations; erreurs d'encodage/collecte propagées sans changement d'objet/metadata, pas de fichier créé ni de save implicite. |
| `TypeError` / `ValueError` / `NotImplementedError` — trims en place | `cut_samples must be an integer, not bool.`; `clip_name must be a string.` (TypeError). Cut hors UInt32, nom vide/NUL/non UTF-8, `In-place trim requires the verified little-endian profile.`; erreurs des helpers de définition/maps/événements/bindings propagées. `In-place trim requires exactly one main audio placement.`; `In-place trim does not support hidden or alternative timeline events.`; `Unsupported in-place trim audio event profile.` (taille35/+33/conteneur mutable); `In-place trim cut must lie strictly inside the clip.`; `Unsupported in-place trim clip profile (expected native UInt8 virtual/UInt24 length, no gain).` (type4/rawunique/liste/nomexact/header/size/tail/offsetnonzero); `In-place trim requires unshifted, defined native timestamps.`; `In-place trim source offset exceeds the verified UInt8 width.`. NotImplementedError : `In-place trim on a playlist containing fades is not verified.`. Encodage de chaque payload terminé avant toute affectation, exceptions propagées sans remplacer les objets/métadonnées. Aucun save, allocation bin ou WAV implicite. |
| `ValueError` — clips et noms | `0x262a`/`0x262c` ambigu, compteur ou définition invalide; `0x2628` tronqué, nom UTF-8 invalide, flag audio/groupe inconnu ou sélecteur de largeur autre que `0x10`/`0x20`/`0x30`/`0x40`; clip absent/ambigu; nouveau nom vide, NUL, non UTF-8, trop long ou déjà présent; ID source inconnu; modèle `0x2629` sans unique identité 48 octets; offset/longueur de sous-clip direct négatif, nul ou hors UInt32; layout offset nul/longueur UInt32 ou offset UInt32/longueur UInt32 non vérifié. |
| `ValueError` — média physique et relink | RIFF/WAVE invalide, big-endian, tronqué, de taille ou d'alignement incohérent; chunk `bext`/`minf`/`regn`/`umid` absent, dupliqué ou trop court; `fmt ` ou `data` du clone/rendu absent, dupliqué ou tronqué; rendu autre que PCM/WAVE_EXTENSIBLE PCM, format PCM incompatible ou taille `data` différente; UMID, stem complet ou abrégé, paire de tokens ou références temporelles `bext`/`regn` invalides ou non concordantes avec le PTX; basename source différent du catalogue PTX ou du stem `regn`; chemin source et destination identiques; extension autre que `.wav`; stems non UTF-8, identiques ou de longueurs UTF-8 différentes; catalogue `0x1004`/`0x103a`, compteurs, noms, ordinaux ou index média invalides/ambigus; suffixe d'enregistrement WAV inconnu ou mélange des variantes `EVAW`/nulle; queue `0x103a` tronquée, sans nœud parent, avec libellé vide/NUL/non UTF-8, marqueur inconnu, terminaison invalide ou compteurs non conformes à `N+1..N+K`, `N+K+2`, `N+K+1`; nouveau nom physique déjà catalogué ou trop long; enregistrements fixes `0x2629` de 48/104 octets impossibles à réassembler ou mal ordonnés; `0x1001`, `0x2628`, ou header source `0x2106` absent, ambigu, tronqué ou de layout inconnu; header `0x2106` source <142 octets; layout source hors du parent/racine spécial ou des flags natifs de production `0x0000`/`0x0001`/`0x2000`/`0x2001`/`0x3000`/`0x3001`/`0x4001`; référence BWF différente de celle du header source; timestamp relink hors UInt32; nouvelle définition de clip en collision; placement exact absent ou ambigu. |
| `ValueError` — relink Premiere | Header source `0x2106` absent, ambigu ou <142 octets; référence BWF du WAV différente de celle lue dynamiquement dans le header source; placement antérieur à `src_offset`; variante virtuelle Premiere autre que les marqueurs observés `0x04`/`0x84` (sélecteur `0x30`, constante `0x08`). |
| Pro Tools — validation externe bloquante | `Could not complete your request because end of stream encountered` : obtenu sur des sorties de relink de clips virtuels Premiere, y compris avec un catalogue hybride créé par import Pro Tools. Ce message démontre que la structure écrite n'est pas encore supportée; il ne faut pas publier ni déployer ce chemin. |
| `ValueError` — builder audio | Manifeste vide; descripteur sans `audio_path` ou `track_name`; nom de piste, filename physique ou nom de clip vide/NUL/non UTF-8; `physical_filename` contenant un chemin ou n'ayant pas l'extension `.wav`; filename physique ou nom de clip dupliqué sans tenir compte de la casse; placement négatif, hors UInt64 ou dont la fin dépasse UInt64; RIFF invalide; `fmt `/`fact`/`data`/`bext` absent, dupliqué, tronqué ou trop court; format autre que WAVE_EXTENSIBLE float mono 48 kHz/32 bits; byte rate, block align, valid bits ou GUID incompatibles; `data` vide/non aligné; `fact` différent de la durée ou durée hors de la largeur du profil; référence BWF hors UInt32; basic UMID nul; template autre que 48 kHz/enum `0x09`, sans piste visible, noms de pistes dupliqués, piste demandée absente, contenant un événement de timeline visible ou caché, prototype/catalogue non unique, lien média non zéro, ou mélange ne correspondant à aucun des profils stricts `15/142/UInt24` et `31/151/UInt32`; catalogue/playlist/clone incohérent; racine ou nombre de définitions générées incohérent; record fixe autre que 48/104 octets; ID de définition ou index média différent de son ordinal; nom de session vide/NUL ou contenant un séparateur; rechargement généré ne concordant pas avec le manifeste. |
| `ValueError` — opérations de montage | Aucun placement visible; plusieurs placements; cut hors du clip ou partagé par plusieurs occurrences; source de split hors layout racine vérifié; coupe/offset source relatif hors UInt24; timestamp de coupe ou nouveau timestamp de Start Trim hors UInt32; payload audio non 35 octets; montant de trim nul/négatif/trop grand; composant d'image invalide ou label Drop Frame interdit. Toute opération transactionnelle restaure l'état avant de relancer l'erreur. |
| `ValueError` — fondus | `0x2630` absent/dupliqué; compteur ou payload `0x262f` invalide; nombre d'événements et géométries différent; ID inconnu/dupliqué; taille géométrique autre que 22/26/27/29/34; profil UInt24 29 octets invalide (`Unsupported 0x262f UInt24 Fade Out layout.`); durées répétées divergentes (`Inconsistent 0x262f Fade Out durations.`); durée UInt24 nulle (`Fade Out duration must be positive.`); association audio absente/ambiguë; début calculé négatif; durée nulle pour crossfade ou >UInt16 lors de la création; type/forme invalide; clip de durée nulle; cible hors clip/ambiguë; fade dépassant les bornes ou déjà existant. |
| `TypeError` / `ValueError` — marqueurs | Session sans playlist principale; règle `0x2030` absente/dupliquée/mal formée; compteur incohérent; payload `0x2077`, longueur ou UTF-8 invalide; index dupliqué/hors `1..65535`; timestamp hors Int64; nom NUL/non UTF-8/trop long; modèle ou zone UUID interne invalide. Pour le filtre `marker_track_name` : argument non `str`, vide ou NUL; catalogue `0x2519 → 0x251b → 0x251c` absent, ambigu, tronqué, dupliqué ou non UTF-8; nom de règle absent; segment final d’assignation `0x2077` absent, de longueur autre que 8, réservé non nul ou ordinal inconnu. |
| `ValueError` — population de règle legacy à l'ajout/suppression | Six nouveaux sites : `Ambiguous marker population catalogue (0x2519).`; `Ambiguous marker population catalogue (0x251b).`; `Unsupported legacy marker population catalogue.` (type, header/count ou items inconnus); `Invalid legacy marker population entry.` (type/payload/ID illisible); `Unsupported legacy marker ruler-zero population entry.` (taille11, six premiers octets nuls, Boolean+6 et ID répété zéro requis); `A unique legacy marker ruler-zero population entry is required.`. Tous les refus précèdent les mutations d'ajout/suppression et de métadonnées TOC. Catalogue optionnel absent accepté sans garantie d'import; no-match de suppression ne modifie/valide pas cette population. |
| `TypeError` / `ValueError` — suppression de markers legacy | `Provide a marker index and/or name.`; index non entier/booléen : `Marker index must be an integer, not bool.`; index hors 1..65535; name non str, NUL/non UTF-8. `Marker deletion requires the verified little-endian profile.`; `Unsupported point-marker ruler block type.`; `Unsupported nested point-marker ruler.`; `Marker deletion requires one verified root marker ruler.`. Erreurs de compteur/container §8 propagées. `Unsupported point-marker deletion profile (...)` : marker shape/type/7items, primary payload, point header/name length, NUL in name, point timestamps (Int64 non négatifs/égaux), child geometry, UUID envelope ou legacy ruler assignment inconnus. `Invalid UTF-8 point-marker name.`; `Duplicate marker index: ...`; `Marker deletion requires a unique 0x0002 pointer table.`; refus de records/table brute §12 propagés; `Conflicting removed marker block types.`. Entrée/profil/table/préparation invalides avant mutation, même pour une recherche sans résultat (hors TOC sans suppression); absence de match/règle vide retournent zéro sans changer objets/metadata, pas une erreur. Exceptions d'encodage/collecte propagées sans mutation. |
| `ValueError` — Clip Gain | Dictionnaire `0x2637` absent/dupliqué/mal formé; compteur trop grand/incohérent ou octets restants; header de record absent/magic invalide, taille déclarée dépassant le payload; header statique non vérifié ou ancienne valeur non finie; header d'enveloppe trois/quatre nœuds (padding/count/flags/segments/réservé) non vérifié, première position non zéro, timestamps non croissants ou valeurs non finies; index de définition hors dictionnaire; gain entrant `NaN`/`+inf` ou hors Float32; payload de clip trop court. Tous les refus précèdent l'écriture. |
| `ValueError` — Volume | Nom de piste vide/NUL/ambigu; association visible→`0x261c` impossible; `0x2619`, `0x260d` ou `0x260a` absent/ambigu/mal formé; magic, taille, padding, terminateur, compteur de nœuds ou segments incohérent; timestamps non strictement croissants; timestamp hors UInt32; valeur non finie ou hors Int16 déci-dB. |
| `TypeError` / `ValueError` / `OverflowError` — remplacement/fusion Volume Audio | Tous les refus de profil/enveloppe du lecteur 10.1/10.3, y compris une autre piste malformée ou largeur/index/identité de stéréo non corroborés. Nom non `str`, vide/NUL/non UTF-8/absent; `replace` non booléen; ordinal non entier/non booléen, négatif ou différent du slot live de piste (pas de playlist); nodes non itérable; paire chaîne/octets, non itérable ou longueur autre que deux; sample booléen/non entier/hors UInt32; doublons entrants; dB booléen/non convertible/non fini, produit non fini ou arrondi hors Int16; remplacement vide (`TypeError`/`ValueError` selon condition). Compteur dépassant `(0xffffffff - 26)//6` : `OverflowError`. Exceptions natives de l'itérateur utilisateur propagées. Aucune mutation avant validation et préparation intégrales; fusion vide/identique autorisée. |
| `TypeError` / `ValueError` — lecture Volume Audio mono/stéréo | `track_name` autre que `str`/`None` (`TypeError`), vide/NUL/non UTF-8 ou absent (`ValueError`); tous les refus de map nommée et des quatre miroirs d'identité. `Unsupported Audio volume profile (...)` pour racine descriptor absente/ambiguë, identités/largeurs/index de playlists/ordinaux de slots non corroborés, compte ou ordre des noms de playlists de canaux divergent; nom absent : `Track not found in verified Audio volume lanes`. Les refus internes de catalogues mono sont propagés ou enveloppés; le validateur mono par défaut n'est pas élargi. `Unsupported mono volume profile (...)` (message privé partagé conservé) pour slots imbriqués, ownership/type du state, géométrie 13 items, ordre/type des lanes ou sélecteurs bruts inconnus, lane Volume autre qu'un unique payload brut; `Invalid verified mono volume envelope (...)` pour magic/taille minimale/padding/terminateur, taille ou compteur nul/incohérent, flags/réservé/segments non vérifiés, timestamps non uniques/croissants. Validation complète avant filtre, y compris enveloppe malformée d'une autre piste; aucun résultat partiel ni mutation. |
| `ValueError` — Clip Groups | Nom vide, NUL ou UTF-8 invalide; groupe absent ou nom déjà défini; `start_samples`/`length_samples` non entier, négatif, nul (durée) ou hors UInt32, ou fin hors UInt32; racines, compteurs, noms UTF-8 ou métadonnées `0x262c`/`0x2428`/`0x2424`/`0x2426` incohérents; template vide hors 48 kHz/enum `0x09`; groupe existant ou piste cible hors profil vide vérifié; prototype non placé/placé plusieurs fois; source audio cible absente ou ambiguë; payload `0x2628`/`0x2523`/`0x2423`/`0x2425` ou queue/flag de macro non conformes; playlist cachée vide/mal formée pour la dissolution ou non vide pour la création audio-backed; enregistrement `0x0002` prototype absent/ambigu; macro visible `00 00 01` dont l'ID ordinal n'existe pas dans `0x262c`. |
| `TypeError` / `ValueError` — lecture Pan principal statique mono | Nom non `str` (`TypeError`), vide/NUL/non UTF-8, absent/ambigu ou cible stéréo (`ValueError`). Tous les refus de resolver named Audio/state/Volume 10.1/10.3 propagés avant lecture Pan. `Unsupported mono static pan profile (...)` : second conteneur mono autre qu'un raw nul de 14 octets; premier autre que vide vérifié ou sélecteur 01/lane unique/suffixe nul 13; ownership/type/lane autre qu'un payload brut unique; cible avec longueur autre que 30/header complet/timestamp zéro/terminateur nul non vérifiés; valeur hors -100..100. Automation de la cible rejetée; courbes des autres monos validées par §10.9 (`Invalid verified mono Pan envelope (...)` si invalides), pas de lecture partielle. Deux conteneurs vides valides retournent None, pas d'erreur ni zéro inventé. |
| `TypeError` / `ValueError` — écriture Pan statique mono | Tous les refus du lecteur et du resolver de cible §10.5 s'appliquent avant mutation. `left` non entier ou booléen : `TypeError`; valeur hors -100..100 : `ValueError`; tout `right` non `None` : `A right Pan channel is not supported by the mono writer.`; cible avec deux conteneurs vides : `Pan writing requires an existing verified static mono lane.`. Aucun ajout de lane ni collapse d'automation; validation des autres pistes même pour une valeur identique. Tous les refus précèdent le remplacement unique. |
| `TypeError` / `ValueError` — lecture Pan statique gauche/droite | Même validation exacte des noms et refus de resolver Audio/state/Volume/mono §10.5. Nom absent/ambigu : `Track not found in verified Audio pan tracks: ...`. `Unsupported stereo static pan profile (...)` pour conteneur 0x260c/type/geometry/sélecteurs/suffixe non vérifiés; lane non directe/type/payload brut unique inconnu; cible avec longueur autre que 30/header complet/timestamp zéro/terminateur non vérifiés ou valeur hors -100..100. Les deux canaux de toutes les stéréo sont validés avant filtre, même pour une cible mono; automation de la cible et stéréo sans deux lanes refusées sans défaut inventé. Les autres stéréo peuvent porter des courbes valides selon §10.11; erreurs `Unsupported stereo Pan automation profile (...)` / `Invalid verified mono Pan envelope (...)` propagées si invalides. Aucune mutation/résultat partiel. |
| `TypeError` / `ValueError` — écriture Pan statique stéréo | Tous les refus communs de nom/Audio/state/Volume/mono et de Pan stéréo §10.7/10.8, y compris une autre piste malformée ou un no-op. `left` non entier/booléen ou hors -100..100 comme §10.6; `right` fourni non entier/booléen : `right must be an integer, not bool, or None.` (`TypeError`); hors plage : `right must be within -100..100.` (`ValueError`). right=None conserve la droite après validation complète, sans création de lane. Toutes les préparations des payloads précèdent les affectations; erreur de préparation propagée sans mutation d'un seul canal. Aucun collapse, UI écrite ou save implicite. |
| `NotImplementedError` | Exactement vingt sites de refus explicites : piste non vide pour `delete_tracks()`; création de groupe vide au-delà de l'ID 255 dans le trailer vérifié; création audio-backed au-delà de la limite de son trailer vérifié; session contenant plus d'un Clip Group pour la dissolution; groupe de dissolution contenant plus d'une piste; groupe de dissolution imbriqué/avec fade/événement non audio; groupe de dissolution placé zéro ou plusieurs fois au lieu d'une; move avec fade attaché; duplicate avec fade attaché; split avec fade attaché; trim avec fade attaché; taille de record Clip Gain hors 0x16/0x26/0x2e; cible d'enveloppe Clip Gain; delete_clip avec lien crossfade; delete_clip nécessitant compaction de plusieurs géométries; delete_clip avec autre profil de fade attaché que le seul UInt24 fade-out vérifié; trim en place sur playlist contenant un fade; conservation de plages sur playlist contenant un fade; import_audio sur session hors little-endian48k/23.976; import_audio sur playlist cible à fade/macro/layout inconnu. Les refus Clip Gain ne modifient aucun record/index; le partage statique est cloné selon §9.3, l'index -1 reçoit un nouveau point selon §9.4. |
| `TypeError` / `ValueError` — lecture d'automation Pan mono | Nom non str : TypeError; vide/NUL/UTF-8 invalide/absent/ambigu : ValueError, mêmes erreurs de nom et resolver Audio/state/Volume/conteneurs mono que §10.5. right non booléen : `right must be a bool.`; right=True sur mono : `A mono Pan automation target has no right channel.`. La branche stéréo est décrite séparément en §10.11. `Invalid verified mono Pan envelope (...)` pour magic/taille minimale/padding/terminateur, taille ou count nul/incohérent, flags/segments/réservés non vérifiés, premier sample autre que zéro/ordre non strict/doublons, ou valeur hors -100..100. Toutes les monos validées avant cible, y compris une autre piste malformée; aucune liste tronquée ni mutation. None pour deux conteneurs mono vides valides n'est pas une erreur ni un nom introuvable. |
| `TypeError` / `ValueError` — lecture d'automation Pan stéréo | Tous les refus communs de nom/Audio/state/Volume et des courbes mono §10.9. right doit être bool; True sur mono : `A mono Pan automation target has no right channel.`. `Unsupported stereo Pan automation profile (...)` pour conteneur/type/sélecteurs/suffixe inconnus, lane non directe/type/payload brut unique invalide. Le décodeur partagé conserve le message historique `Invalid verified mono Pan envelope (...)` pour header/size/count/flags/segments/terminateur/timestamps/valeurs de l'une ou l'autre lane stéréo. Deux lanes obligatoires; toute autre stéréo ou le canal non sélectionné malformé bloque le résultat d'une cible stéréo. Une cible mono garde ses témoins opaques. Pas de liste partielle/default inventé/mutation. |
| `TypeError` / `ValueError` — remplacement d'automation Pan mono/stéréo | Tous les refus de nom/Audio/state/Volume/conteneurs/enveloppes mono §10.9 et, pour cible stéréo, des deux canaux de toutes les stéréo §10.11/10.12 avant mutation. right non booléen : `right must be a bool.`; True sur mono : `A mono Pan automation target has no right channel.`; absence de lane : `Pan automation writing requires an existing verified lane.`. nodes non iterable/paires chaînes ou octets/mal formées : TypeError; sample non entier ou booléen : TypeError, hors UInt32 : ValueError; timestamp entrant dupliqué : ValueError; pan non entier/booléen : TypeError, hors -100..100 : ValueError; entrée vide : `At least one Pan node is required for replacement.`. Premier timestamp autre que zéro rejeté par le décodeur §10.9. Exceptions natives d'itération/tri/encodage propagées sans mutation; résultat et payload préparés avant une affectation. |
| `OverflowError` | Offset de bloc sérialisé hors UInt32; payload `PTBlock` hors champ de taille UInt32; bloc dépassant l'espace fichier UInt32; enveloppe Volume ou Pan mono/stéréo reconstruite dépassant la taille de bloc UInt32; timestamp restauré d'un groupe ou fin calculée d'un placement de Clip Group hors UInt64; nouvel index de point Clip Gain hors Int32 signé; nouvel ID de clip, index média de relink ou compteurs de noms physiques hors UInt32. |
| `FileNotFoundError` | Fichier d'entrée absent (propagé nativement); dossier de destination inexistant pour `xor_session()`/`save()`; WAV source ou WAV de remplacement absent, ou dossier du nouveau WAV inexistant pour `relink_clip()`; template/WAV source du builder absent ou parent du dossier de livraison inexistant. |
| `FileExistsError` | Le chemin du nouveau WAV demandé à `relink_clip()` existe déjà; le dossier cible du builder existe avant l'appel ou apparaît pendant la génération. Aucun écrasement n'est permis. |
| `PermissionError` et autres `OSError` natifs | Erreur d'ouverture/lecture/création/remplacement du système de fichiers; elles conservent leur sous-type. La recherche facultative dans `Audio Files` est la seule exception : ses `OSError` sont absorbées et la résolution continue avec `0x1004`. |
| `OSError` explicite | Écriture chiffrée plus courte que la taille attendue. Le fichier temporaire est supprimé et la destination existante reste intacte. |
