using System.Reflection;

// Slay the Spire 2 程序集的反射探针.
//
// 游戏附带的 XML 文档只覆盖部分 API, 所需的卡牌伤害 / 格挡和敌人意图伤害
// 位于未公开文档的成员中. 加载 sts2.dll 并打印实际成员, 避免猜测接口.
//
// 用法: dotnet run -- <game data dir>

var dataDir = args.Length > 0
    ? args[0]
    : @"C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2\data_sts2_windows_x86_64";

AppDomain.CurrentDomain.AssemblyResolve += (_, e) =>
{
    var simple = new AssemblyName(e.Name).Name + ".dll";
    var candidate = Path.Combine(dataDir, simple);
    return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
};

var asm = Assembly.LoadFrom(Path.Combine(dataDir, "sts2.dll"));
Console.WriteLine($"assembly: {asm.GetName().Name} {asm.GetName().Version}");

Type? Find(string fullName) => asm.GetType(fullName, throwOnError: false);

void DumpMembers(Type? type, string label, Func<MemberInfo, bool>? filter = null)
{
    Console.WriteLine($"\n=== {label} ({type?.FullName ?? "NOT FOUND"}) ===");
    if (type is null) return;

    foreach (var p in type.GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
        if (filter is null || filter(p))
            Console.WriteLine($"  prop  {p.PropertyType.Name,-22} {p.Name}");

    foreach (var f in type.GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
        if (filter is null || filter(f))
            Console.WriteLine($"  field {f.FieldType.Name,-22} {f.Name}");

    foreach (var m in type.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static)
                 .Where(m => !m.IsSpecialName))
        if (filter is null || filter(m))
            Console.WriteLine($"  meth  {m.ReturnType.Name,-22} {m.Name}({string.Join(", ", m.GetParameters().Select(x => x.ParameterType.Name))})");
}

bool Interesting(MemberInfo m) =>
    m.Name.Contains("Var", StringComparison.OrdinalIgnoreCase) ||
    m.Name.Contains("Damage", StringComparison.OrdinalIgnoreCase) ||
    m.Name.Contains("Block", StringComparison.OrdinalIgnoreCase) ||
    m.Name.Contains("Description", StringComparison.OrdinalIgnoreCase) ||
    m.Name.Contains("Preview", StringComparison.OrdinalIgnoreCase);

DumpMembers(Find("MegaCrit.Sts2.Core.Models.CardModel"), "CardModel", Interesting);
DumpMembers(Find("MegaCrit.Sts2.Core.Entities.Players.Player"), "Player");
DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVarSet"), "DynamicVarSet");
DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVar"), "DynamicVar");
DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.DamageVar"), "DamageVar"); DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.RepeatVar"), "RepeatVar"); DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.CardsVar"), "CardsVar"); DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.PowerVar`1"), "PowerVar");
DumpMembers(Find("MegaCrit.Sts2.Core.Localization.DynamicVars.BlockVar"), "BlockVar", Interesting);

// DynamicVars 实际上可能定义在 AbstractModel 中.
DumpMembers(Find("MegaCrit.Sts2.Core.Models.AbstractModel"), "AbstractModel", Interesting);

// 意图类型层次缺少文档, 因此枚举所有具体类型.
var intentBase = Find("MegaCrit.Sts2.Core.MonsterMoves.Intents.AbstractIntent");
if (intentBase is not null)
{
    Console.WriteLine("\n=== AbstractIntent (all members) ===");
    DumpMembers(intentBase, "AbstractIntent");

    var attack = Find("MegaCrit.Sts2.Core.MonsterMoves.Intents.AttackIntent");
    var calc = attack?.GetProperty("DamageCalc")?.PropertyType;
    if (calc is not null)
    {
        Console.WriteLine($"\nDamageCalc = {calc}");
        if (calc.IsGenericType)
            foreach (var arg in calc.GetGenericArguments())
                Console.WriteLine($"  generic arg: {arg}");
    }
    var repeats = attack?.GetProperty("Repeats");
    Console.WriteLine($"Repeats type: {repeats?.PropertyType}");
    Console.WriteLine("\n=== concrete intents ===");
    Type[] types;
    try { types = asm.GetTypes(); }
    catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t is not null).ToArray()!; }

    foreach (var t in types.Where(t => t is not null && intentBase.IsAssignableFrom(t) && t != intentBase))
    {
        Console.WriteLine($"\n--- {t.Name} ---");
        foreach (var p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance))
            Console.WriteLine($"  prop  {p.PropertyType.Name,-22} {p.Name}");
    }
}


