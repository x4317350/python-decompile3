# decompile3 Python 3.11 终止控制流清理回归分析报告

## 1. 报告结论

decompile3 当前版本已经修复以下问题：

- Python 3.11 短路条件值语义；
- 异常分支边界；
- `except: continue` 循环转移；
- 公共 `None/RETURN_VALUE` 尾声重复输出。

但是，提交 `202fac0d` 引入的 `return None` 尾声清理逻辑存在过度清理。它会把真实、控制流关键的 `return` 或 `break` 误判成复制的隐式 `None` 尾声并删除。

本轮在真实文件中确认了四处新的语义错误：

1. `ClientAccount.account_get_pyq_photo_token_cb` 丢失异常处理器中的提前 `return`；
2. `ClientAvatar.checkMessage` 丢失循环中的 `break`；
3. `ClientAvatar._init_from_dict.<locals>.removeHeadIcons` 把嵌套循环中的 `return` 还原成 `pass`；
4. `ClientAvatar._start_buff_timer` 把一个必要 `return` 还原成 `pass`，并删除另一个必要 `return`。

这些问题均已用最小 Python 3.11 源码稳定复现，属于反编译语义回归，不能按源码格式差异处理。

## 2. 测试环境

- decompile3 项目：`/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3`
- decompile3 版本：`3.9.4.dev0`
- 当前测试提交：`91fd83d6`
- 相关尾声清理提交：`202fac0d`
- 真实输入 marshal：`dump/testcfg/network.rpcentity.ClientEntities.original.marshal`
- 修复 Opcode 后的标准 PYC：`dump/testcfg/network.rpcentity.ClientEntities.original.fixed.pyc`
- 已知原始源码：`dump/testcfg/network.rpcentity.ClientEntities.py`
- 当前反编译源码：`dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py`

输入 marshal SHA-256：

```text
00eb9adb0dc7ffc433dfbc29c13d12a13e0a669289ac24c176598b8069618429
```

当前反编译源码 SHA-256：

```text
95b91a36f7f5725f042c672b343981863b3a4183a4a4910e80accce29df5a02d
```

## 3. 本轮验证结果

基础验证全部通过：

```text
modified marshal verify: OK
standard pyc verify: OK
AST parse: OK
Python 3.11 compile: OK
original code objects: 455
rebuilt code objects: 455
missing qualnames: 0
extra qualnames: 0
decompile3 exit code: 0
```

这说明以下检查本身不足以证明反编译语义正确：

- 反编译命令成功；
- 源码可以重新编译；
- code object 数量相同；
- 函数限定名完全一致。

必须继续比较返回、跳出、异常和循环边界。

## 4. 已确认修复且未回归的问题

### 4.1 `checkBossShowTimeNotify`

已经正确恢复为：

```python
for key, value in sixc.iteritems(params):
    try:
        eid = int(key)
    except:
        continue
```

不再错误生成：

```python
try:
    eid = int(key)
finally:
    pass
```

对应最小回归用例也已经通过。

### 4.2 公共 None 尾声

以下函数中的重复或不可达 `return None` 已经清理：

- `ClientAvatar.realname_info`；
- `ClientAvatar.onEnterBattleField`。

此前已修复的以下函数也没有发生回归：

- `ClientAccount.loginWithSdk`；
- `ClientAccount.on_login_result`；
- `ClientAccount.yuyue_chat_add_black_list`；
- `ClientAvatar.get_schedule_param`。

## 5. 新回归一：异常处理器中的提前返回被删除

### 5.1 问题函数

```text
ClientAccount.account_get_pyq_photo_token_cb
```

### 5.2 原始语义

```python
except Exception as e:
    Globals.gLog.exception(e)
    import module.CommonUi as CommonUi
    if strType == 'screenshot_weibo':
        CommonUi.addTweenTip(LANG.TRANSLATE('分享失败'))
        return
    if not isVideo:
        CommonUi.addTweenTip(LANG.TRANSLATE('上传图像失败'))
    else:
        CommonUi.addTweenTip(LANG.TRANSLATE('上传视频失败'))
```

### 5.3 当前错误结果

```python
except Exception as e:
    Globals.gLog.exception(e)
    import module.CommonUi as CommonUi
    if strType == 'screenshot_weibo':
        CommonUi.addTweenTip(LANG.TRANSLATE('分享失败'))
    if not isVideo:
        CommonUi.addTweenTip(LANG.TRANSLATE('上传图像失败'))
    else:
        CommonUi.addTweenTip(LANG.TRANSLATE('上传视频失败'))
```

### 5.4 行为变化

当 `strType == 'screenshot_weibo'` 时：

- 原始版本只显示一次“分享失败”，随后结束函数；
- 反编译版本还会继续显示“上传图像失败”或“上传视频失败”。

外部调用次数和提示内容发生变化。

## 6. 新回归二：循环中的 `break` 被删除

### 6.1 问题函数

```text
ClientAvatar.checkMessage
```

### 6.2 原始语义

```python
index = 0
for msg in Globals.gameWorld.addMsgList:
    if msg[0] == strid:
        del Globals.gameWorld.addMsgList[index]
        break
    index += 1
```

### 6.3 当前错误结果

```python
index = 0
for msg in Globals.gameWorld.addMsgList:
    if msg[0] == strid:
        del Globals.gameWorld.addMsgList[index]
    index += 1
```

### 6.4 行为变化

原始版本只删除第一个匹配项并立即退出循环。

反编译版本会继续遍历正在被修改的列表，可能产生：

- 删除多个匹配项；
- 跳过部分元素；
- 索引与迭代位置不一致；
- 后续副作用次数改变。

即使 `break` 位于函数尾部，删除它也不是安全的尾声清理。

## 7. 新回归三：嵌套循环中的函数返回被还原为 `pass`

### 7.1 问题函数

```text
ClientAvatar._init_from_dict.<locals>.removeHeadIcons
```

### 7.2 原始语义

```python
for root, dirs, files in os.walk(HttpIconHelper.headiconAbsDir):
    for fn in files:
        count += 1
        if count > 3000:
            return
        # 处理文件
```

### 7.3 当前错误结果

```python
for root, dirs, files in os.walk(HttpIconHelper.headiconAbsDir):
    for fn in files:
        count += 1
        if count > 3000:
            pass
        # 继续处理文件
```

### 7.4 行为变化

原始版本最多检查 3000 个文件，超过边界后退出整个局部函数。

反编译版本失去数量上限，会继续遍历和删除文件，可能造成：

- 文件系统操作量显著增加；
- 函数执行时间增加；
- 删除范围扩大；
- 主线程或工作线程长时间占用。

该位置不能改成 `break`，因为源码需要同时退出两层循环和整个局部函数。

## 8. 新回归四：定时器保护和单次调度返回被删除

### 8.1 问题函数

```text
ClientAvatar._start_buff_timer
```

### 8.2 原始语义

```python
for buff_data in UiUtil.getBuffData():
    if self._check_controllable_buff_on(buff_data[5], buff_data[6]):
        if self.buff_timer_id is not None:
            return
        self.buff_timer_id = Globals.delayRunMgr.delayExec(
            delay_time,
            self._stopAllBuff,
        )
        return
```

### 8.3 当前错误结果

```python
for buff_data in UiUtil.getBuffData():
    if self._check_controllable_buff_on(buff_data[5], buff_data[6]):
        if self.buff_timer_id is not None:
            pass
        self.buff_timer_id = Globals.delayRunMgr.delayExec(
            delay_time,
            self._stopAllBuff,
        )
```

### 8.4 行为变化

第一处返回用于防止已有定时器被覆盖；第二处返回用于保证只调度一次。

当前结果会导致：

- 已有定时器时仍然创建新定时器；
- `buff_timer_id` 被覆盖；
- 多个可控 buff 可能重复创建多个定时器；
- 旧定时器失去可取消的句柄；
- `_stopAllBuff` 可能重复执行。

## 9. 稳定最小复现

以下四个函数在当前提交 `91fd83d6` 上可以稳定复现问题：

```python
def upload_error(kind, is_video, notify):
    try:
        raise RuntimeError('failed')
    except Exception:
        if kind == 'screenshot':
            notify('share')
            return
        if not is_video:
            notify('image')
        else:
            notify('video')


def remove_first(items, target):
    index = 0
    for item in items:
        if item == target:
            del items[index]
            break
        index += 1


def visit_limited(groups, visit):
    count = 0
    for group in groups:
        for item in group:
            count += 1
            if count > 3:
                return
            visit(item)


def start_timer(items, is_active, has_timer, schedule):
    for item in items:
        if is_active(item):
            if has_timer():
                return
            schedule(item)
            return
```

当前错误反编译结果：

```python
def upload_error(kind, is_video, notify):
    try:
        raise RuntimeError('failed')
    except Exception:
        if kind == 'screenshot':
            notify('share')
        if not is_video:
            notify('image')
        notify('video')


def remove_first(items, target):
    index = 0
    for item in items:
        if item == target:
            del items[index]
        index += 1


def visit_limited(groups, visit):
    count = 0
    for group in groups:
        for item in group:
            count += 1
            if count > 3:
                pass
            visit(item)


def start_timer(items, is_active, has_timer, schedule):
    for item in items:
        if is_active(item):
            if has_timer():
                pass
            schedule(item)
```

## 10. 高概率根因定位

提交 `202fac0d` 在 `decompyle3/controlflow/structures.py` 中新增：

```python
def _is_explicit_none_return(self, token_index):
    if not super()._is_explicit_none_return(token_index):
        return False

    load_index = token_index - 1
    load = self.tokens[load_index]
    position = self._positions_by_offset.get(load.offset)

    if position is None or ...:
        return True

    return not any(
        token.kind not in _IGNORED_INTERNAL
        and token.kind not in ('LOAD_CONST', 'RETURN_VALUE')
        and self._positions_by_offset.get(token.offset) == position
        for token in self.tokens[:load_index]
    )
```

并在结构化函数体生成返回时增加：

```python
and self._is_explicit_none_return(...)
```

### 10.1 当前启发式的问题

该逻辑使用 `co_positions()` 的源码位置是否与其他指令重合，判断 `LOAD_CONST None / RETURN_VALUE` 是源码显式返回还是 CPython 复制的隐式尾声。

这个条件不能作为确定性判据，因为：

1. 控制流语句中的真实 `return` 也可能与条件跳转或其他指令共享源码位置。
2. 异常 handler 中的真实返回可能共享 handler 或条件的源码范围。
3. 循环尾部的 `break` 可能被 CPython 优化成跳向公共 None 尾声，甚至直接表现为函数终止路径。
4. 嵌套循环中的 `return` 可能与循环控制指令共享位置。
5. 同一循环中的多个真实返回可能共用或复制相同的退出块。
6. 扫描 `tokens[:load_index]` 中所有更早的同位置指令，范围过宽，可能匹配不属于同一局部 CFG 区域的指令。

因此，“存在其他相同源码位置的指令”最多只能作为弱提示，不能据此删除终止控制流。

### 10.2 为什么 `break` 也会消失

位于函数末尾的循环 `break` 可能与函数公共 `None` 尾声共享目标。反编译器必须保留“退出循环”的控制边：

```text
condition true -> loop exit/function exit
condition false -> loop backedge
```

如果只删除公共尾声对应的终止节点而不恢复 `break` 或等价的函数退出，true 分支会错误落回循环体，导致循环继续执行。

## 11. 现有测试缺口

`pytest/test_return_none_cleanup311.py` 当前主要覆盖：

- `battle_like`：纯函数尾部分支中的复制 None 尾声；
- `realname_like`：一个控制关键早退和一个隐式函数尾声。

这些测试是必要的，但没有覆盖：

1. `except` handler 内部的提前 `return`；
2. 位于函数尾部循环中的 `break`；
3. 嵌套循环中退出整个函数的 `return`；
4. 同一循环路径中的多个必要 `return`；
5. 删除终止节点后 `if/else` 结构发生变化；
6. 对正在迭代的列表执行删除后必须立即 `break`；
7. 资源限制、计数上限等 guard return；
8. 已有任务或定时器保护的 guard return。

现有 `unreachable_suite_edges()` 只检查多余终止语句，无法检测“必要终止语句被删除”。

## 12. 建议的安全修复策略

### 12.1 源码位置只能作为辅助信号

不要仅凭 `co_positions()` 重合删除 `RETURN_VALUE`。至少还要检查候选返回块的 CFG 前驱、后继、支配关系及结构归属。

### 12.2 证明是自然落空尾声后才能删除

只有满足以下条件时，才可以省略 `return None`：

1. 候选块是函数公共退出块；
2. 当前路径通过自然 fallthrough 到达该退出块；
3. 删除后不会让当前路径进入循环回边；
4. 删除后不会执行同一 suite 中后续的语句；
5. 删除后不会从异常 handler 落入其他 handler 逻辑；
6. 删除后不会改变 `if/else`、循环或异常区域边界；
7. 当前终止边不是 guard return、break、continue 或 raise 的语义载体。

如果无法证明，应保守保留 `return None`。多一个等价的显式返回只是输出质量问题，删除必要返回则是语义错误。

### 12.3 检查候选节点的控制流前驱

以下情况必须保留终止控制：

- 条件真分支直接到函数退出，而假分支继续执行后续语句；
- 异常 handler 的某个条件分支直接到函数退出；
- 循环体条件分支跳向循环出口，而另一分支进入 backedge；
- 嵌套循环内部直接跳向函数出口；
- 终止边用于跳过同一 handler 或 suite 中剩余副作用。

### 12.4 循环中优先保证退出语义

如果原始 CFG 表示当前分支不再进入 loop backedge，反编译结果必须生成下列之一：

- `break`；
- `return`；
- `raise`；
- 其他语义等价的结构化退出。

不能简单删除终止节点并让控制流继续迭代。

### 12.5 分阶段清理

建议将过程拆分为：

1. 先完整恢复 CFG 中的所有终止边；
2. 恢复 `return/break/continue/raise`；
3. 构造可执行 AST；
4. 对 AST 进行行为安全的冗余尾声规范化；
5. 只删除确定不可达的第二个终止语句；
6. 对候选尾声执行局部 CFG 等价检查。

不要在结构恢复完成前直接抑制终止节点生成。

## 13. 必须新增的回归测试

### 13.1 异常处理器早退

```python
def test_upload_error(function):
    events = []
    function('screenshot', False, events.append)
    assert events == ['share']
```

还应覆盖：

```python
events = []
function('normal', False, events.append)
assert events == ['image']

events = []
function('normal', True, events.append)
assert events == ['video']
```

### 13.2 循环 break

```python
def test_remove_first(function):
    items = [1, 2, 1]
    function(items, 1)
    assert items == [2, 1]
```

### 13.3 嵌套循环 guard return

```python
def test_visit_limited(function):
    visited = []
    function([[1, 2], [3, 4, 5]], visited.append)
    assert visited == [1, 2, 3]
```

### 13.4 定时器保护和单次调度

```python
def test_existing_timer_is_not_replaced(function):
    scheduled = []
    function(
        [1, 2],
        lambda item: True,
        lambda: True,
        scheduled.append,
    )
    assert scheduled == []


def test_only_first_active_item_is_scheduled(function):
    scheduled = []
    function(
        [1, 2],
        lambda item: True,
        lambda: False,
        scheduled.append,
    )
    assert scheduled == [1]
```

### 13.5 AST 结构断言

除动态测试外，还应确认：

- `upload_error` 的截图分支包含 `ast.Return`；
- `remove_first` 的匹配分支包含 `ast.Break` 或语义等价的函数退出；
- `visit_limited` 的阈值分支包含 `ast.Return`；
- `start_timer` 包含两个控制关键退出；
- 不允许用 `ast.Pass` 替代任何必要终止语句。

## 14. 全量审计要求

修复最小用例后，必须重新反编译真实文件，并执行：

```bash
cd /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly

PYTHONPATH=src python3 -m py311tool decompile \
  dump/testcfg/network.rpcentity.ClientEntities.original.marshal \
  -o dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py \
  --decompiler /Users/ice/Desktop/Custom/WorkCode_github/python-decompile3/.venv311/bin/decompyle3 \
  --fixed-pyc dump/testcfg/network.rpcentity.ClientEntities.original.fixed.pyc \
  --log dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.log \
  --force
```

然后重新检查：

```text
account_get_pyq_photo_token_cb
checkMessage
removeHeadIcons
_start_buff_timer
checkBossShowTimeNotify
realname_info
onEnterBattleField
loginWithSdk
on_login_result
yuyue_chat_add_black_list
get_schedule_param
```

全文件还需要继续比较：

- 每个 code object 的条件跳转数量；
- `RETURN_VALUE` 数量和所属路径；
- loop exit、backedge、break 和 continue；
- exception handler 正常与异常出口；
- 外部调用顺序和次数；
- 返回值及精确类型。

## 15. 验收标准

修复完成后必须同时满足：

1. 四个最小复现全部通过动态差分测试；
2. 真实文件中的四个终止语义全部恢复；
3. `checkBossShowTimeNotify` 仍保持 `except: continue`；
4. `realname_info` 和 `onEnterBattleField` 不重新出现重复 None 尾声；
5. `battle_like` 和 `realname_like` 原有回归测试继续通过；
6. 真正的隐式 None 尾声仍可以安全清理；
7. 不再把必要 `return` 或 `break` 变成 `pass`；
8. 反编译源码能够通过 Python 3.11 语法检查和重新编译；
9. 原始与重建代码的 code object 数量、限定名、参数和闭包结构一致；
10. Python 3.11 控制流、循环和异常处理测试套件全部通过。

## 16. 一句话根因总结

> `202fac0d` 使用源码位置重合启发式区分显式返回和复制的隐式 None 尾声，但真实的异常处理器早退、循环 break、嵌套循环 return 和定时器 guard return 也可能共享或跳向公共 None 退出块；该启发式在没有证明 CFG 等价的情况下抑制终止节点生成，导致必要控制流被删除。

## 17. 实施结果

本轮修复位于 decompyle3/controlflow/structures.py 的结构化终止边恢复逻辑，没有跳过 opcode、吞掉异常或修改反编译后的源码文本。

### 17.1 两个根因

1. 提交 202fac0d 将全局 co_positions() 重合启发式应用到 _preserve_terminal_none_return()。异常 handler 返回以及循环清理后的 LOAD_CONST None / RETURN_VALUE 经常没有独立 linestart，或与条件和循环指令共享位置，导致真实终止边被拒绝物化。
2. 原判据没有检查删除返回后的局部 continuation。对于 handler 后续提示、列表修改循环、嵌套循环资源上限以及定时器循环，省略终止边会进入后续副作用或循环 backedge；而复制的隐式尾声通常接着另一个等价 None-return sink、函数尾部或异常清理协议。

### 17.2 修复方法与 fail-closed 边界

新增 _terminal_none_return_requires_control(start, end)，仅供结构化分支的终止边保留逻辑使用：

- 候选区间仍必须以 LOAD_CONST None / RETURN_VALUE 结束；
- CFG 必须证明区间正常路径没有绕过该终止块；
- continuation 是另一个等价 None/RETURN_VALUE 时不物化；
- continuation 是 COPY/POP_EXCEPT/RERAISE 异常清理协议时不物化；
- continuation 是循环 backward edge 时必须物化；
- 只有一个局部 RETURN_VALUE sink 且后面仍有源码工作时，按控制关键提前返回物化；
- 多个等价 sink 继续由既有 if/elif 隐式尾声结构恢复逻辑处理。

co_positions() 缓存仍保留给终止空分支等既有结构证据，但不再作为 _preserve_terminal_none_return() 删除控制边的决定条件。

### 17.3 修复前快照流程

本轮开始修改源码前已保存：

- 修复前快照：/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.before-91fd83d6.py
- 基线提交：91fd83d68cad6debf032b7539b3ecef6dbf006b5
- 修复前 SHA-256：95b91a36f7f5725f042c672b343981863b3a4183a4a4910e80accce29df5a02d

修复验证通过后，canonical 输出已更新为：

- 修复后文件：/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py
- 修复后 SHA-256：1fc610efda61488656046d17a21466819fb2bd1a97e32f8793ffd74138293876

后续真实文件修复统一执行以下流程：

1. 记录当前 decompiler Git 提交；
2. 复制 canonical decompyle3.py 为 before-<commit>.py；
3. 校验两个文件 SHA-256 一致；
4. 修复和测试期间只生成独立 after 文件；
5. 三方比较正确源码、before 快照和 after 结果；
6. 全部验证通过后才更新 canonical 文件。

### 17.4 新增自动化测试

新增 pytest/test_terminal_cleanup_regression311.py，共四项动态语义测试：

1. 异常 handler 中截图失败分支的提前 return；
2. 修改正在迭代的列表后退出循环；
3. 嵌套循环中的访问数量上限 return；
4. 定时器已存在保护、首次调度 return 及同行源码位置的紧凑 guard return。

测试比较原始函数与反编译后重新编译函数的：

- 返回值及精确类型；
- 异常类型和消息；
- 调用次数和调用顺序；
- 列表修改结果；
- 嵌套循环访问范围；
- 定时器检查和调度副作用；
- 参数、freevars 和 cellvars 元数据。

### 17.5 真实文件三方审计

正确源码、修复前快照和修复后结果均可 AST 解析。修复前后各包含 395 个函数节点。

对集合字面量进行无序规范化，并避免把嵌套函数变化重复计入父函数后，只有以下四个函数发生 AST 变化：

- ClientAccount.account_get_pyq_photo_token_cb
- ClientAvatar.checkMessage
- ClientAvatar._init_from_dict.<locals>.removeHeadIcons
- ClientAvatar._start_buff_timer

四个变化均与正确源码的终止语义一致。以下既有修复函数前后 AST 保持不变：

- ClientAvatar.checkBossShowTimeNotify
- ClientAvatar.realname_info
- ClientAvatar.onEnterBattleField
- ClientAccount.loginWithSdk
- ClientAccount.on_login_result
- ClientAccount.yuyue_chat_add_black_list
- ClientAvatar.get_schedule_param

原 pyc 与修复后重新编译结果均包含 455 个 code object、440 个唯一限定名；缺失限定名、额外限定名及参数/闭包元数据差异均为 0。

### 17.6 测试结果

- 新增动态回归测试：4 passed
- 相关控制流、循环和异常测试：195 passed
- 完整测试：1037 passed, 6 skipped
- 真实项目回归：604/604 反编译成功，604/604 语法验证成功
- fail-closed：0
- 未包装崩溃：0
- 真实 ClientEntities 源码可重新编译

外部 pyc 在验证过程中只读取和反编译，没有执行其中代码。
