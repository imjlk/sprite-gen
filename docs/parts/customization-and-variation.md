# 파츠 커스터마이징과 체형별 베리에이션 계획

> Owns: Proposed customization slots, variation catalogs, fitting bindings and acceptance scope · Index: [docs/README.md](../README.md)

SD explorer의 기본 커스터마이징은 헤어·표정·상의·하의·신발이다. 이 다섯 가지를 각각 바꿀 수 있어야 첫 사용자 데모가 완료된다. 모자, 얼굴 장식, 겉옷, 장갑, 등 장비 등도 초기 계약에서 정의할 수 있어야 하며, 지원 범위는 profile과 catalog 데이터가 결정한다.

2026-10-04 사용자 정정: **기본 재생 방식은 공통 모션 위의 런타임 파츠 레이어 합성이다.** idle/walk/run은 공통 모션 3개이며, 외형을 바꿀 때 현재 state/sample/elapsed time을 유지하고 선택한 attachment만 교체한다. 32외형 × 3상태 = 96은 조합·가림을 확인하는 검증 케이스 수다. 96개의 완성 클립을 제작·저장하는 요구가 아니다. 조합별 사전 bake는 기본 데모나 기본 자산 형식으로 사용하지 않고, `parts-build`는 명시적으로 요청한 export 용도로 유지한다.

이 문서는 2026-10-04 사용자 요구를 반영한 구현 계획이다. [초기 실행 계획](initial-implementation-and-demo.md)의 두 slot 데모를 내부 smoke로 두고, 사용자 데모와 후속 베리에이션 목표를 확장한다. PR01에서 group/slot/preset/palette와 체형별 binding의 첫 선언 검증을 구현했고, humanoid 초기 catalog는 기본 5종을 포함한 20개 slot을 제공한다. 실제 지원 범위는 [구현된 계약](01-contract.md)을 따른다. 아래의 추가 slot·아트 수량·합성·UI·승인은 후속 구현·제작 목표다.

2026-10-05 사용자 검수로 기본 구현을 단순화했다. **온전한 기본 몸 + 눈 + 헤어 + 의상**을 순서대로 그린다. 위치 기준점은 머리·몸통의 상대좌표를 알려줄 뿐이며, 몸을 분해하거나 파츠를 변형하는 지시가 아니다. 기본 몸 프레임은 파츠를 모두 꺼도 그대로 남는다. 현재 데모는 눈·헤어·상의 각 2종을 독립 선택하며, [8외형·세 동작을 확인한 checkpoint](initial-implementation-and-demo.md)를 남겼다. 하의·신발은 16포즈 그림 후보를 제작했으나 전체 fitting과 binding은 남아 있다. 기본 5종 각 2개의 32외형 목표를 유지하며, 아직 그 완료로 판단하지 않는다. 앞서 분리·재질 투영으로 제작한 18slot 데모는 철회했으며, 그 기술 검사 수량을 아트 완료로 세지 않는다.

다리 자체는 기본 몸의 영상 스프라이트에 이미 들어 있는 동작을 따른다. 바지·신발의 포즈가 달라져야 한다면 필요한 **파츠의 포즈 그림**을 추가한다. 허벅지/종아리 회전·길이 보정·무릎 solver를 늘려 대응하지 않는다. 파츠 디자인 수와 포즈 수는 별도 축이며, 포즈 그림은 공통 motion sample에서 모든 외형이 공유한다. 다른 체형도 그 체형의 기준 몸과 상대좌표·포즈 그림을 명시적으로 준비하는 방식으로 확장한다.

## 선택과 렌더링을 나누는 데이터 구조

| 단위 | 의미 | 예 |
|---|---|---|
| group | UI에서 찾기 쉬운 선택 영역 | 얼굴, 헤어, 의상, 장비 |
| slot/subslot | 사용자가 독립적으로 선택하는 자리 | hair.style, face.eyes, clothing.upper, equipment.back |
| piece | 한 선택을 렌더링하는 내부 그림 조각 | 뒷머리/앞머리, torso/sleeve_l/sleeve_r |
| design variant | 실루엣·형태·표현이 다른 디자인 | 숏컷/단발, 반바지/긴바지, 무표정/미소 |
| palette channel | 같은 형태에서 바꿀 수 있는 색 영역 | 피부, 머리, 천의 주색/보조색, 가죽, 금속 |
| binding | 특정 rig/motion/view에 맞춘 piece와 sample map | 같은 bob 디자인의 SD fitting과 tall fitting |
| preset/bundle | 여러 slot 선택을 한 번에 적용하는 명시적 묶음 | 미소 표정 preset, 탐험가 복장 set |

piece는 바로 선택 가능한 slot으로 승격되지 않는다. 예를 들어 jacket 선택은 torso와 좌우 소매를 함께 바꾼다. 사용자가 소매만 바꾸게 하려면 해당 subslot과 torso/cuff 접합 interface를 catalog에 선언한다. 동일한 방식으로 hairstyle 안의 front/back pieces와 독립 선택 가능한 bangs subslot을 구분한다.

TopologyProfile은 신체 node, socket, 교체 interface와 허용 slot을 선언한다. RigVariant는 실제 비율·canvas·style·view를 정한다. `CustomizationCatalog`는 group/slot 구조, 선택 가능한 PartPack reference, palette channel, 조합 규칙과 preset을 선언한다. AppearanceSpec은 catalog의 선택을 해석한 정확한 PartPack/binding과 색상 값을 갖는다. 실제 이미지의 immutable snapshot 게시와 합성은 PR02–04가 담당한다.

renderer는 slot 이름으로 사람·동물을 분기하지 않는다. 모든 선택을 동일한 piece/node/socket/mask/draw-list로 해석한다. `semantic_role` 같은 검색·UI용 문자열은 열린 vocabulary이며, 호환 판정은 정확한 rig/motion/view와 interface로 한다.

content digest 참조 방향은 topology → rig → motion → PartPack/binding → catalog → appearance다. topology나 rig는 자신에게 binding된 PartPack 또는 catalog digest를 다시 참조하지 않는다. catalog snapshot과 적용한 preset·조합 정책도 BuildLock의 입력에 고정한다.

## SD explorer의 선택 가능한 범위

다음은 SD profile의 목표 catalog다. 다른 profile에 이 목록을 필수로 요구하지 않는다. M1의 실제 제작 의무는 뒤의 단계별 표에 정의한다.

| group | 선택 항목 | 세분화와 내부 pieces | 베리에이션 예 |
|---|---|---|---|
| 기본 외형 | skin palette, markings slot | 피부 color region, 얼굴/몸체의 marking mask | 피부색, 주근깨, 문신, 흉터 |
| 헤어 | hairstyle, bangs, facial_hair | front/back/side/tied piece; bangs는 호환 interface가 있는 경우 독립 선택 | 숏컷, 단발, 포니테일, 장발; 앞머리, 수염 |
| 표정 | expression preset | eyes_l/r, brows_l/r, mouth, 필요 시 cheeks | 무표정, 미소, 화남, 슬픔, 놀람 |
| 얼굴 세부 | eyes, eyebrows, mouth | expression preset의 독립 override 가능 subslot | 눈 모양·눈색, 눈썹, 입 모양 |
| 얼굴 장비 | eyewear, facewear, ear_accessory_l/r | glasses front/arms, mask, 좌우 귀 장식 | 안경, 고글, 마스크, 귀걸이 |
| 머리 장비 | headwear | cap/hat front/back, visor, 필요 시 hair coverage mask | 모자, 투구, 머리띠 |
| 상체 | upper, outerwear, collar, sleeves_l/r | torso, front/back panels, 좌우 소매, hood/collar | 티셔츠, 재킷, 후디, 코트 |
| 하체 | lower, belt, legwear_l/r | waist/pelvis, 좌우 leg, skirt front/back | 반바지, 긴바지, 치마, 벨트, 양말 |
| 손과 팔 | gloves_l/r, wrist_l/r, arm_guard_l/r | 손·손목·팔의 raster pieces | 장갑, 팔찌, 보호대 |
| 발 | footwear_l/r | 좌우 발과 필요 시 boot cuff/leg coverage | 운동화, 부츠, 샌들 |
| 몸통 장비 | neckwear, chest_accessory, waist_accessory | neck front/back, chest/waist attachment | 목걸이, 스카프, 배지, 파우치 |
| 등 | back_accessory | back body/strap, cloak front/back | 백팩, 망토, 날개 |
| 들고 있는 장비 | hand_item_l/r | grip/pivot, near/far component | 도구, 방패, 무기 |
| 비인간형 부속 | ears_l/r, horns, tail, wings | node replacement와 overlay pieces | 동물 귀, 뿔, 꼬리, 날개 |

tail/wings 같은 node가 없는 인간 rig에 이 slot을 자동으로 노출하지 않는다. 필요한 appendage node/socket을 제공하는 별도 topology/rig를 선언한 경우 같은 catalog 방식으로 추가한다. 피부색과 털색은 profile에 따라 의미가 다른 palette channel이다.

헤어의 앞/뒤, 상의의 소매, 좌우 신발 등은 실제로 세분화할 수 있어야 한다. M1 catalog는 완성 hairstyle/jacket/pair 선택을 우선 제공하고, 독립 subslot은 접합 규격을 갖춘 pack에만 노출한다. 미제작 subslot이나 binding이 없는 변형은 지원 상태를 표시하고 선택·release를 차단한다.

## 표현과 모션의 조합

표정은 locomotion state와 별개인 appearance 선택이다. `state=run, expression=smile`을 사용할 수 있어야 하며, 모든 표정을 `run_smile` 같은 신규 motion state로 복제하지 않는다. 표정 preset은 eyes/brows/mouth의 선택 묶음을 해석한다. advanced 선택에서 입만 바꾸면 해당 subslot의 선택만 바뀌고 표정 표시를 custom으로 바꾼다. preset을 다시 선택하면 선언한 face subslot 목록을 재적용한다.

모든 face piece도 현재 MotionClip의 sample에 binding된다. 얼굴 방향·head pose가 같을 때는 검증된 rigid/held 그림을 명시적으로 재사용할 수 있다. head pose나 가림이 다르면 해당 sample/view의 별도 raster와 mask가 필요하다. 포니테일, 치마, 꼬리의 secondary motion도 sample별 그림과 draw-order를 가진다.

M1 데모는 같은 motion clock에서 표정·장비를 바꾸고 현재 sample과 경과 시간을 유지한다. sample별 blink/표정 전환도 이 clock의 sample map을 따른다. 독립 표정 시계를 만들지 않는다. 특정 게임 엔진의 GPU/mesh adapter는 후속 범위이며, 기본 데모의 파츠 교체 재생을 후속 단계로 미루지 않는다.

## 조합 정책과 세트

각 slot은 selection cardinality, 기본 선택, empty 허용 여부, 적용 node/socket, 교체·overlay interface를 선언한다. 생략된 선택은 고정된 catalog의 default를 사용하고, 명시적인 `None`은 empty policy를 적용한다. empty 시 body의 기본 piece를 복원할지 optional piece를 그리지 않을지도 slot 데이터로 선언한다. 모자·백팩 같은 장비를 제거해도 필수 신체 node는 제공돼야 한다.

PartPack이나 preset은 자신의 변경 범위를 명시한다. jacket은 torso와 선언한 sleeves를, 장갑 pair는 좌우 gloves를, 전신복은 upper/lower의 선택을 점유할 수 있다. 단일 slot 조작은 그 footprint 밖의 선택·원본 asset·색상·motion을 바꾸지 않는다. 합성 결과에서 다른 piece의 visibility가 바뀌는 경우에는 선언한 mask/draw-order와 검수 의존성을 적용한다. preset이 여러 slot을 바꾸는 경우에는 바뀌는 목록을 보여준다.

M1 preset은 slot별 assignment 목록으로 정의하고 재귀 preset 호출은 지원하지 않는다. 적용 순서는 catalog default → 선택한 preset → 명시적 subslot override이며, 마지막 resolved 선택을 대상으로 occupies/requires/excludes/join 조건을 검사한다. 적용 과정에서 사용한 default와 override도 재현할 수 있도록 기록한다.

| 규칙 | 의미 | 검증 예 |
|---|---|---|
| occupies | 선택이 점유하는 slot 목록 | jumpsuit가 upper와 lower를 함께 점유 |
| requires | 필요한 node/socket/interface 또는 선택 조건 | hand item은 grip socket, boot는 해당 cuff interface 필요 |
| excludes | 함께 선택할 수 없는 조합 | 같은 자리를 쓰는 hood-up과 helmet |
| covers/mask | 가리는 대상과 sample별 coordinate space | hat이 hair.front의 일부를 가림; 머리를 통째로 제거하지 않음 |
| replaces/provides | 기존 그림 제거와 유지할 신체 interface | boots가 기본 foot_l/r 그림을 대체 |
| join_interface | 독립 subslot 사이의 접합 규격 | bangs와 hair.back, sleeve와 torso, glove와 cuff |
| symmetry_group | 연결된 좌우 선택과 해제 정책 | shoe pair 기본 연결, asymmetric pack에서 개별 선택 |

색상이나 tag가 같다는 사실만으로 fitting을 승인하지 않는다. 동시 선택 불가능, mask 부재, 접합 interface 불일치는 명시적 오류다. 기본 충돌 정책은 reject이며, 다른 항목의 자동 해제·대체는 명시적으로 선택한 정책으로만 수행하고 resolved appearance에 남긴다.

한 위험 pair가 통과했다고 다른 piece가 추가된 조합의 가림까지 승인된 것은 아니다. coverage가 조합의 draw-order/mask에 영향을 받으면 최종 appearance 단위 검수가 필요하다. 승인되지 않은 조합의 preview에는 상태를 표시하며 release는 승인 scope를 검사한다.

## 제작 단계와 실제 데모 완료 기준

| 단계 | 실제 SD pack의 의무 | 완료 의미 |
|---|---|---|
| 내부 smoke | upper 2 × footwear 2, 세 상태, 12 clip | 초기 피팅·교체 경로 확인; 사용자 커스터마이징 데모 완료 판정에는 사용하지 않음 |
| 기본 사용자 데모 | 공통 모션 3개 + hair/expression/upper/lower/footwear 각 2종 | 재생 중 5slot 독립 교체, clock 유지, 32외형·96 상태 조합 검증; 조합별 완성 clip 저장 없음 |
| M1 추가 장비 | headwear/gloves/back_accessory 각각 `None`과 1종 | 기존 5slot과 함께 교체 가능, 위험 pair/triple 검수 기록 |
| M2 SD wardrobe | hair 4, expression 5, upper 3, lower 3, footwear 3; 추가 장비·세부 subslot 확장 | 제작·부분 재생성·검수 workflow로 catalog를 확장하고 승인 coverage를 기록 |

기본 사용자 데모의 디자인 목표는 다음과 같다. 색만 바꾼 항목을 새 shape variant 수량으로 세지 않는다.

| 기본 선택 | variant A | variant B | 내부 piece의 예 |
|---|---|---|---|
| 헤어 | 짧은 textured hair | 단발 | front/back/side |
| 표정 | 무표정 | 미소 | 눈·눈썹·입; preset과 독립 subslot |
| 상의 | 청록색 짧은 jacket | 다른 collar/pocket/cuff 형태의 주황색 jacket | torso, sleeve_l/r |
| 하의 | 반바지 | 긴바지 | waist/pelvis, leg_l/r |
| 신발 | 낮은 운동화 | ankle boots | foot_l/r, 필요 시 cuff |

표정과 헤어를 바꾸기 위해 실제 기본 head에는 hair/eyes/brows/mouth가 분리돼 있어야 한다. 하의·신발 교체도 기본 의상·발 그림을 제거할 수 있는 자료가 필요하다. 합쳐진 body에서 일부 영역을 crop했다고 가려졌던 살색이나 신체 그림이 복원되는 것으로 처리하지 않는다.

M1 추가 장비가 모두 기본 다섯 slot과 양립한다고 가정하면 후보 공간은 256외형·768 상태 조합이다. 이는 제작할 clip 수가 아니다. 기본 pack의 96 상태 조합을 런타임으로 검사하고, 추가 장비는 유효 조합의 pair coverage와 다음 위험 triple을 별도로 검사한다.

- hair × headwear × expression: 얼굴·앞머리 visibility와 착용 위치.
- upper × gloves × hand pose: 손목 경계와 hand/sleeve 순서.
- upper × back_accessory × arm pose: strap, 소매, 팔의 앞뒤 관계.
- lower × footwear × contact sample: 바짓단·boot cuff와 발 접점.

M2의 기본 다섯 slot만으로 후보 공간은 `4 × 5 × 3 × 3 × 3 = 540`외형, 세 상태 기준 1620 clip이다. body pieces와 part raster는 필요한 디자인·pose별로 제작하고, 기본적으로 이 카테시안 곱 전체를 미리 굽지 않는다. 승인된 concrete appearance와 요청 states만 build하며, pairwise 검수·위험 triple·사용자 지정 preset의 coverage를 구분해 기록한다. pairwise 통과를 전 조합의 시각적 승인으로 확대하지 않는다.

## 다른 체형으로 확장하는 계약

| 형태 | 사용자 선택으로 제공할 범위 | 같은 원리로 분해할 piece | fitting 요구 |
|---|---|---|---|
| SD humanoid | hair/expression/upper/lower/footwear, headwear/gloves/back 등 | hair front/back, face features, torso/sleeves, 양쪽 leg/foot | SD rig/motion/view binding |
| tall humanoid | 동일 기본 5slot과 해당 rig의 장비 | 같은 node interface, 긴 sleeve/leg, glove/cuff | 같은 design ID라도 tall용 별도 raster·binding |
| quadruped | coat/pattern, expression, ears, tail, neckwear, torso gear, pawwear | 좌우 귀, muzzle/eyes/mouth, 꼬리, saddle/strap, 네 발 | fore/hind·left/right ID 유지, 각 발의 binding |
| root-only blob | surface/pattern, eyes/mouth/expression, crown ornament, body accessory | 얼굴과 surface region, 필요한 attachment | 다리·신발·사람 upper/lower를 필수로 요구하지 않음 |
| 비대칭 또는 6limb fixture | 선언된 appendage와 장비 자리만 노출 | profile에 있는 임의 개수의 node와 piece | 좌우 pair나 limb 수를 하드코딩하지 않음 |

공유하는 것은 catalog의 선택·variant·palette·bundle·compatibility 계약과 renderer다. 동일한 PNG를 여러 체형에 자동으로 맞춘다는 의미가 아니다. 다른 머리 크기, 긴 팔/다리, muzzle, 네 발은 각 rig의 최종 pose와 interface에 맞는 자료가 필요하다.

design ID는 같은 디자인이라는 정체성을 유지하고, concrete binding은 rig digest/motion digest/view로 구분한다. 예를 들어 bob hairstyle은 SD와 tall에 두 binding을 가질 수 있다. tall binding이 없으면 unsupported로 표시하고 제작 계획에 추가한다. SD 그림을 resize해 호환이라고 기록하지 않는다.

신체 비율·사지 길이·관절 배치를 바꾸는 body shape는 별도 RigVariant와 적합한 MotionSet으로 처리한다. 색·marking·복장 선택은 현재 rig 안의 변형이다. 체형 변경 시 기존 선택은 design ID로 유지하려 시도하되, destination rig의 binding이 없는 항목을 missing으로 표시하고 build를 차단한다. 임의 기본 복장으로 대체하지 않는다.

M2의 다른 체형 acceptance는 슬롯 하나의 교체로 끝내지 않는다. tall은 기본 5slot 각각에 실제 2종 binding을 확보하고, canine은 coat/pattern·expression·ears·tail·neckwear·torso gear에서 실제 2종 선택을 확보한다. 세 상태 모두 같은 resolver와 승인 경로를 사용한다. 전체 조합은 생성된 유효 pair coverage, 위험 triple, named appearance 단위로 검수 범위를 보고한다.

## PR별 반영 위치

| PR | 추가할 설계와 완료 기준 |
|---|---|
| 01 | SlotDefinition/CustomizationCatalog, group/subslot/cardinality, palette channel, preset, footprint/compatibility 참조, asymmetric/6limb fixture의 순수 파싱 |
| 02 | 얼굴·머리·waist·손목·발·appendage의 최종 geometry annotations를 profile 데이터로 처리; 필요한 socket만 검증 |
| 03 | 기본 hair/face/clothing 제거가 가능한 body piece 등록, multi-piece variants, join interface, 여러 rig의 binding 수용·불일치 거절 |
| 04 | 공통 timeline의 런타임 파츠 합성·교체, 선택·preset·mask·replacement, 5slot/독립 subslot/palette와 96 상태 조합 검증 |
| 05 | catalog/preset/palette/mask 포함 승인 subject, 조합·slot footprint 변화의 invalidation, 검수 coverage report |
| 06 | 요청된 appearance만 export, 기본 96상태 사례과 추가 장비 case의 승인 범위 확인, 조합 전수 bake 금지 |
| 07 | design × rig × view × motion/sample × piece의 missing coverage 계획; palette-only 변경은 이미지 생성 작업 없음 |
| 08 | 기존 provider로 지정한 variant와 누락 piece/sample만 후보 제작; 미확정 제출 상태와 locked 결과 보존 |
| 09 | catalog로 group/기본·세부 선택 UI 생성; preset 변경 범위, pair 연결·해제, unavailable binding, 충돌 이유 표시 |
| 10 | 확장된 SD wardrobe와 tall/canine의 다중 slot variation acceptance; 승인된 실제 조합 수와 미검증 영역 공개 |
| 12 | 같은 motion clock에서 expression·세부 파츠·preset 변경, 실제 게임 adapter의 capability 검증 |

PR01/03/04의 synthetic fixture는 첫 artwork의 제작량보다 넓은 모델 범위를 검증한다. accessory나 세부 UI의 제작이 늦다는 이유로 이를 계약 밖의 특수 문자열·하드코딩으로 처리하지 않는다.

## 최소 회귀 시나리오

| 입력 또는 조작 | 기대 결과 |
|---|---|
| hair/eyes/mouth/upper/lower/footwear 하나씩 교체 | 선언한 slot footprint 밖의 선택과 motion hash 유지 |
| expression preset 적용 뒤 mouth만 override | 해당 입 선택만 변경, 눈·눈썹·모션은 유지 |
| head node 또는 얼굴 node 없는 profile | head/face slot을 요구하지 않고 선언된 선택만 표시 |
| jacket 소매 또는 bangs subslot의 join mismatch | import/resolve 오류 |
| cap + hair + expression의 동시 선택 | target mask와 face visibility가 각 sample에서 확인됨 |
| jumpsuit + 독립 pants 선택 | 명시적 충돌 오류, 또는 사용자가 선택한 conflict policy에 따른 결과 기록 |
| 피부/헤어/천 palette 변경 | 이미지 생성 0건, 출력과 approval subject는 새 revision |
| human shoe pair와 canine 네 발의 footwear pack | 동일 resolver, profile이 선언한 node 수만큼 처리 |
| 같은 design의 SD→tall 변경 | destination binding이 있을 때 유지, 없으면 missing |
| side-specific 귀·신발·hand item을 mirror | 명시적 해당 view 정책/binding 없으면 거절 |
| face/hair/upper의 run sample 하나 누락 | 해당 sample/piece를 재작업 대상으로 계획, 완성 release 차단 |
| 실제 선택된 PartPack/preset/catalog 수정 | 이전 lock은 고정 snapshot을 읽고, 새 appearance는 재검증 |

모든 schema/operation 테스트는 API 없이 실행한다. 실제 raster 피팅과 자연스러운 움직임은 별도 loop 검수로 기록한다. 여기에서 정의한 최소 variation 수량을 채우기 전에는 두 slot smoke를 전체 커스터마이징 데모로 보고하지 않는다.
